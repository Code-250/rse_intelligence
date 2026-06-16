"""
Signal engine — runs after each new report is parsed.
Checks for trading opportunities and generates alerts.
"""
import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import get_conn
from datetime import date, timedelta


def check_dividend_capture(conn, report_date):
    """Alert when a dividend record date is within 5 trading days."""
    signals = []
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, description, amount, record_date, payment_date
        FROM corporate_actions
        WHERE action_type = 'dividend'
        AND record_date IS NOT NULL
        AND record_date BETWEEN %s AND %s
    """, (report_date, report_date + timedelta(days=7)))
    for row in cur.fetchall():
        sym, desc, amount, rec_date, pay_date = row
        msg = (
            f"DIVIDEND CAPTURE: {sym}\n"
            f"Amount: RWF {amount}/share\n"
            f"Record date: {rec_date} — buy BEFORE this date\n"
            f"Payment: {pay_date}"
        )
        signals.append({"type": "dividend_capture", "symbol": sym, "message": msg})
    cur.close()
    return signals


def check_order_book_squeeze(conn, report_date):
    """Alert when bids exist but no offers — price likely to rise."""
    signals = []
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, SUM(quantity) as total_bid
        FROM order_book
        WHERE report_date = %s AND side = 'bid'
        GROUP BY symbol
    """, (report_date,))
    bid_symbols = {row[0]: row[1] for row in cur.fetchall()}

    cur.execute("""
        SELECT symbol FROM order_book
        WHERE report_date = %s AND side = 'offer'
    """, (report_date,))
    offer_symbols = {row[0] for row in cur.fetchall()}

    for sym, qty in bid_symbols.items():
        if sym not in offer_symbols and qty > 0:
            msg = (
                f"ORDER BOOK SQUEEZE: {sym}\n"
                f"Bids: {qty:,} shares waiting — ZERO sellers\n"
                f"Price likely to rise when sellers appear. Watch closely."
            )
            signals.append({"type": "squeeze", "symbol": sym, "message": msg})
    cur.close()
    return signals


def check_price_drop(conn, report_date):
    """Alert on significant single-day price drops (buying opportunity)."""
    signals = []
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, name, closing_price, previous_price, change_pct
        FROM equity_prices
        WHERE report_date = %s
        AND change_pct IS NOT NULL
        AND change_pct <= -3
    """, (report_date,))
    for row in cur.fetchall():
        sym, name, close, prev, chg = row
        msg = (
            f"PRICE DROP ALERT: {sym} ({name})\n"
            f"Dropped {chg}% today: {prev} → {close} RWF\n"
            f"Potential buy opportunity — check fundamentals."
        )
        signals.append({"type": "price_drop", "symbol": sym, "message": msg})
    cur.close()
    return signals


def check_bond_vs_equity_yield(conn, report_date):
    """Alert when bond coupon rate significantly beats equity dividend yield."""
    signals = []
    cur = conn.cursor()
    cur.execute("""
        SELECT AVG(coupon_rate) FROM bond_trades
        WHERE report_date = %s AND coupon_rate IS NOT NULL
    """, (report_date,))
    row = cur.fetchone()
    if row and row[0]:
        avg_bond_yield = float(row[0])
        if avg_bond_yield >= 13:
            msg = (
                f"BOND OPPORTUNITY: Average T-Bond coupon today: {avg_bond_yield:.1f}%\n"
                f"Risk-free RWF returns. Consider rotating idle cash to T-Bonds."
            )
            signals.append({"type": "bond_yield", "symbol": None, "message": msg})
    cur.close()
    return signals


def save_signals(conn, signals, report_date):
    """Save signals to DB without alerting. Deduplicates by (date, type, symbol)."""
    cur = conn.cursor()
    for sig in signals:
        cur.execute("""
            INSERT INTO signals (signal_date, signal_type, symbol, message, alerted)
            VALUES (%s, %s, %s, %s, FALSE)
            ON CONFLICT (signal_date, signal_type, symbol) DO NOTHING
        """, (report_date, sig["type"], sig.get("symbol"), sig["message"]))
    conn.commit()
    cur.close()


def run_signals(report_date=None):
    """Run signal checks and save to DB. Does NOT send WhatsApp."""
    if report_date is None:
        report_date = date.today()
    conn = get_conn()
    all_signals = []
    all_signals += check_dividend_capture(conn, report_date)
    all_signals += check_order_book_squeeze(conn, report_date)
    all_signals += check_price_drop(conn, report_date)
    all_signals += check_bond_vs_equity_yield(conn, report_date)

    print(f"  Signals found for {report_date}: {len(all_signals)}")
    if all_signals:
        save_signals(conn, all_signals, report_date)
    conn.close()
    return all_signals




if __name__ == "__main__":
    run_signals()
