"""
Master pipeline:
1. Pull ALL emails from *@mocapital.co.rw, download PDF attachments
2. Classify each PDF (daily report / trade confirmation / statement / announcement)
3. Parse each PDF with the right parser
4. Write structured data to database
5. Run signal engine → WhatsApp alerts

Usage:
  python run_pipeline.py                          # full pipeline
  python run_pipeline.py --file /path/to/file.pdf # process single file
  python run_pipeline.py --classify-only          # classify inbox without parsing
"""
import sys
import os

sys.path.append(os.path.dirname(__file__))

import pdfplumber
from db import init_db, get_conn
from extractor.gmail_extractor import run as pull_emails
from parser.classifier import classify_inbox, classify_and_move
from parser.pdf_parser import parse_pdf
from parser.db_writer import write_report
from parser.trade_parser import parse_trade_confirmation
from parser.statement_parser import parse_account_statement
from signals.engine import run_signals
from alerts.advisory import send_advisory


def record_document(conn, filename, doc_type, email_subject="", email_date=""):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO raw_documents (filename, doc_type, email_subject, email_date)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (filename) DO UPDATE SET doc_type = EXCLUDED.doc_type
    """, (filename, doc_type, email_subject, email_date))
    conn.commit()
    cur.close()


def mark_parsed(conn, filename, error=None):
    cur = conn.cursor()
    cur.execute("""
        UPDATE raw_documents SET parsed = %s, parse_error = %s WHERE filename = %s
    """, (error is None, error, filename))
    conn.commit()
    cur.close()


def get_pdf_text(filepath):
    with pdfplumber.open(filepath) as pdf:
        text = ""
        for page in pdf.pages:
            text += (page.extract_text() or "") + "\n"
    return text


def process_daily_report(filepath, conn):
    data = parse_pdf(filepath)
    result = write_report(data)
    print(f"    Equity: {result['equity']}, FX: {result['fx']}, Bonds: {result['bonds']}")
    return data.get("report_date")


def process_trade_confirmation(filepath, conn):
    text = get_pdf_text(filepath)
    data = parse_trade_confirmation(text)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO my_trades (trade_date, symbol, side, quantity, price_per_share,
            total_consideration, brokerage_fee, settlement_date, source_file)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        data["trade_date"], data["symbol"], data["side"], data["quantity"],
        data["price_per_share"], data["total_consideration"], data["brokerage_fee"],
        data["settlement_date"], os.path.basename(filepath)
    ))
    conn.commit()
    cur.close()
    print(f"    Trade: {data['side']} {data['quantity']} x {data['symbol']} @ {data['price_per_share']}")
    return None


def process_account_statement(filepath, conn):
    text = get_pdf_text(filepath)
    data = parse_account_statement(text)
    cur = conn.cursor()
    for holding in data["holdings"]:
        cur.execute("""
            INSERT INTO my_holdings (statement_date, symbol, quantity, current_value,
                cash_balance, total_portfolio_value, source_file)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            data["statement_date"], holding["symbol"], holding["quantity"],
            holding["current_value"], data["cash_balance"],
            data["total_portfolio_value"], os.path.basename(filepath)
        ))
    conn.commit()
    cur.close()
    print(f"    Statement: {len(data['holdings'])} holdings, cash={data['cash_balance']}")
    return None


def process_announcement(filepath, conn):
    # Announcements are stored as raw text — no structured parse yet
    print(f"    Announcement stored (no structured parse)")
    return None


PARSERS = {
    "daily_report": process_daily_report,
    "trade_confirmation": process_trade_confirmation,
    "account_statement": process_account_statement,
    "announcement": process_announcement,
    "unknown": lambda p, c: print("    Skipping unknown document type"),
}


def process_file(filepath, doc_type, conn):
    filename = os.path.basename(filepath)
    print(f"\n  [{doc_type}] {filename}")
    try:
        parser = PARSERS.get(doc_type, PARSERS["unknown"])
        result = parser(filepath, conn)
        mark_parsed(conn, filename)
        return result
    except Exception as e:
        print(f"    ERROR: {e}")
        mark_parsed(conn, filename, error=str(e))
        return None


def run_full_pipeline():
    print("=== RSE Intelligence Pipeline ===\n")

    print("Step 1: Initializing database...")
    init_db()
    conn = get_conn()

    print("\nStep 2: Pulling emails from Gmail...")
    new_files = pull_emails()
    for f in new_files:
        record_document(conn, f["filename"], "inbox", f.get("email_subject", ""), f.get("email_date", ""))

    print("\nStep 3: Classifying all PDFs in inbox...")
    classified = classify_inbox()
    for item in classified:
        filename = os.path.basename(item["path"])
        record_document(conn, filename, item["doc_type"])

    print("\nStep 4: Parsing classified documents...")
    dates_to_signal = []
    for item in classified:
        d = process_file(item["path"], item["doc_type"], conn)
        if d:
            dates_to_signal.append(d)

    print("\nStep 5: Running signal engine...")
    sorted_dates = sorted(set(dates_to_signal))
    latest_signals = []
    for d in sorted_dates:
        signals = run_signals(d)
        for s in signals:
            s["date"] = d
        if d == sorted_dates[-1]:
            latest_signals = signals  # Only alert on the most recent date

    print("\nStep 6: Generating AI advisory and sending WhatsApp...")
    latest_date = sorted_dates[-1] if sorted_dates else None
    if latest_date:
        # Only send advisory for the latest/today's report — not historical backfill
        send_advisory(latest_date, latest_signals, conn)
    else:
        print("  No market data processed — no advisory sent.")

    conn.close()
    print(f"\n=== Done. {len(classified)} documents processed. ===")


def run_single_file(filepath):
    init_db()
    conn = get_conn()
    new_path, doc_type, _ = classify_and_move(filepath)
    record_document(conn, os.path.basename(new_path), doc_type)
    d = process_file(new_path, doc_type, conn)
    if d:
        signals = run_signals(d)
        for s in signals:
            s["date"] = d
        send_advisory(d, signals, conn)
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--file":
        run_single_file(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "--classify-only":
        init_db()
        classify_inbox()
    else:
        run_full_pipeline()
