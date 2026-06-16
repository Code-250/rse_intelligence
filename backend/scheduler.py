"""
Daily scheduler — the only process that needs to run permanently.

Schedule:
  13:15 Kigali time — check for new MO Capital emails, run pipeline, send advisory if warranted
  08:00 Kigali time — run RL outcome checker (updates signal weights from last week's predictions)

On Railway: this is the single process that runs as a background worker.
Locally: python3 scheduler.py
"""

import schedule
import time
import sys
import os
from datetime import date

sys.path.append(os.path.dirname(__file__))

from db import init_db, get_conn
from extractor.gmail_extractor import run as pull_new_emails
from parser.classifier import classify_inbox, classify_and_move
from parser.pdf_parser import parse_pdf
from parser.db_writer import write_report
from parser.trade_parser import parse_trade_confirmation
from parser.statement_parser import parse_account_statement
from signals.engine import run_signals
from signals.scorer import should_send, record_predictions, update_weights_from_outcomes
from alerts.advisory import send_advisory
import pdfplumber


# ── document processors ───────────────────────────────────────────────────────

def get_pdf_text(filepath):
    with pdfplumber.open(filepath) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def process_file(filepath, doc_type, conn):
    filename = os.path.basename(filepath)
    try:
        if doc_type == "daily_report":
            data = parse_pdf(filepath)
            result = write_report(data)
            print(f"    Equity={result['equity']} FX={result['fx']} Bonds={result['bonds']}")
            return data.get("report_date")

        elif doc_type == "trade_confirmation":
            from parser.trade_parser import parse_trade_confirmation
            text = get_pdf_text(filepath)
            data = parse_trade_confirmation(text)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO my_trades
                    (trade_date, symbol, side, quantity, price_per_share,
                     total_consideration, brokerage_fee, settlement_date, source_file)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (data["trade_date"], data["symbol"], data["side"], data["quantity"],
                  data["price_per_share"], data["total_consideration"],
                  data["brokerage_fee"], data["settlement_date"], filename))
            conn.commit()
            cur.close()
            print(f"    Trade: {data['side']} {data['quantity']}x {data['symbol']}")

        elif doc_type == "account_statement":
            from parser.statement_parser import parse_account_statement
            text = get_pdf_text(filepath)
            data = parse_account_statement(text)
            cur = conn.cursor()
            for h in data["holdings"]:
                cur.execute("""
                    INSERT INTO my_holdings
                        (statement_date, symbol, quantity, current_value,
                         cash_balance, total_portfolio_value, source_file)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (data["statement_date"], h["symbol"], h["quantity"],
                      h["current_value"], data["cash_balance"],
                      data["total_portfolio_value"], filename))
            conn.commit()
            cur.close()

        # Mark as parsed in raw_documents
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO raw_documents (filename, doc_type, parsed)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (filename) DO UPDATE SET parsed = TRUE
        """, (filename, doc_type))
        conn.commit()
        cur.close()

    except Exception as e:
        print(f"    ERROR processing {filename}: {e}")
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO raw_documents (filename, doc_type, parsed, parse_error)
            VALUES (%s, %s, FALSE, %s)
            ON CONFLICT (filename) DO UPDATE SET parse_error = EXCLUDED.parse_error
        """, (filename, doc_type, str(e)))
        conn.commit()
        cur.close()

    return None


# ── core daily job ────────────────────────────────────────────────────────────

def daily_pipeline():
    print(f"\n{'='*50}")
    print(f"RSE Intelligence — Daily Run {date.today()}")
    print(f"{'='*50}")

    conn = get_conn()

    # 1. Pull ONLY new emails (skips already-processed ones)
    print("\n[1] Checking Gmail for new MO Capital emails...")
    new_files = pull_new_emails(conn)
    if not new_files:
        print("  No new emails. Nothing to process today.")
        # Still check RL outcomes even on quiet days
        conn.close()
        return

    print(f"  {len(new_files)} new attachment(s) to process")

    # 2. Classify new PDFs
    print("\n[2] Classifying new documents...")
    classified = classify_inbox()

    # 3. Parse each document
    print("\n[3] Parsing documents...")
    dates_with_data = []
    for item in classified:
        d = process_file(item["path"], item["doc_type"], conn)
        if d:
            dates_with_data.append(d)

    if not dates_with_data:
        print("  No new market data extracted.")
        conn.close()
        return

    # 4. Run signal engine on new dates only
    latest_date = max(dates_with_data)
    print(f"\n[4] Running signal engine for {latest_date}...")
    signals = run_signals(latest_date)

    # 5. Decide whether to send (scorer + RL weights)
    print("\n[5] Scoring signals...")
    worth_sending, reason, scored = should_send(signals, conn)
    print(f"  Decision: {'SEND' if worth_sending else 'SILENT'} — {reason}")

    # 6. Send advisory only if warranted
    if worth_sending:
        print("\n[6] Generating and sending advisory...")
        send_advisory(latest_date, signals, conn)
        record_predictions(conn, signals, latest_date)
    else:
        print("\n[6] Skipping advisory — not worth messaging investor today.")
        print(f"  Reason: {reason}")

    conn.close()
    print(f"\nDone.")


# ── RL outcome check (runs every morning) ─────────────────────────────────────

def morning_rl_check():
    print(f"\n[RL] Running Q-learning trainer for {date.today()}...")
    conn = get_conn()
    from rl.trainer import run_daily_training
    run_daily_training(conn)
    conn.close()


# ── scheduler setup ───────────────────────────────────────────────────────────

def run_scheduler():
    init_db()
    print("RSE Intelligence Scheduler started.")
    print("  Daily pipeline: 13:15 (after RSE market closes at 12:00)")
    print("  RL weight update: 08:00 daily")
    print("  Press Ctrl+C to stop.\n")

    # Market closes at noon Kigali time — run pipeline 1h15 after to get report
    schedule.every().day.at("13:15").do(daily_pipeline)

    # Check outcomes from 7 days ago every morning
    schedule.every().day.at("08:00").do(morning_rl_check)

    # Run immediately on first start to catch up on any missed emails
    print("Running initial pipeline check...")
    daily_pipeline()

    while True:
        schedule.run_pending()
        time.sleep(60)  # check every minute


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--now":
        # Manual trigger: python3 scheduler.py --now
        init_db()
        daily_pipeline()
    elif len(sys.argv) > 1 and sys.argv[1] == "--rl":
        # Manual RL check: python3 scheduler.py --rl
        init_db()
        morning_rl_check()
    else:
        run_scheduler()
