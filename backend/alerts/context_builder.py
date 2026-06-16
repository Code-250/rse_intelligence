"""
Builds rich market context from our historical database.
This is what makes the AI advisory accurate and Rwanda-specific.
The model doesn't need to know Rwanda — we tell it everything.
"""
from datetime import date, timedelta


RSE_KNOWLEDGE = """
RWANDA STOCK EXCHANGE — ANALYST REFERENCE (always apply this knowledge):

LISTED COMPANIES:
- BOK/BK: Bank of Kigali. Rwanda's largest bank. Very liquid by RSE standards. Tracks economic growth closely.
- BLR: BRALIRWA. Heineken subsidiary. Brews Primus, Mutzig. Dividend-paying stock. Consumer staple — defensive.
- MTNR: MTN Rwandacell. Telecom. Tracks subscriber growth and ARPU. Less volatile than others.
- IMR: I&M Bank Rwanda. Mid-size bank. Less liquid than BK.
- CMR: CIMERWA. Only cement producer listed. Infrastructure proxy — rises with construction activity.
- EQTY: Equity Bank Rwanda. Kenyan bank subsidiary. Tracks Equity Group (Nairobi) performance.
- KCB: KCB Rwanda. Kenyan bank subsidiary.
- USL: unlisted/small cap.

MARKET CHARACTERISTICS:
- RSE is extremely illiquid. Most days see zero or near-zero equity trades.
- Total daily equity turnover is often 0 RWF. This is NORMAL — do not interpret as a crisis.
- Bond market is far more active than equity (T-bonds dominate volume).
- Order book imbalances (many bids, zero offers) are common and signal pent-up demand.
- Bid-offer spreads are wide. Patience is required for execution.
- Settlement is T+3 (trade date plus 3 business days).
- Market hours: Monday–Friday, 9:00–12:00 Kigali time.

DIVIDEND CONTEXT:
- Dividends are announced by board, approved at AGM, then paid.
- Record date = last day to own shares to qualify.
- Dividend yields on RSE stocks often exceed 5–10%, making dividend capture a viable strategy.
- Withholding tax on dividends: 15% for residents, varies for non-residents.
- BRALIRWA pays dividends annually, usually Q2/Q3.
- BK Group pays interim + final dividend.

CURRENCY CONTEXT:
- All RSE transactions in Rwandan Franc (RWF).
- USD/RWF has been depreciating ~5–8% annually. This benefits exporters (CMR exports cement).
- KES/RWF relevant for cross-listed stocks (EQTY, KCB) — track Nairobi for price signals.

BOND MARKET:
- Government T-bonds dominate. Coupon rates 12–14% — very high by global standards.
- When bond yields are 13%+, equity dividend yields must compete or capital rotates to bonds.
- T-bonds are considered risk-free in RWF terms.
- Yield curve: longer maturities pay more. 20-year bonds pay more than 5-year.

INVESTOR BEHAVIOUR PATTERNS:
- Many RSE investors are long-term holders (pension funds, SACCOs, diaspora).
- Retail participation is low but growing.
- Price movements are slow and deliberate — illiquidity prevents fast moves.
- Corporate announcements (dividends, AGM) are the most reliable price catalysts.

WHAT MAKES A GOOD SIGNAL:
- Dividend record date within 5 days = urgent, time-sensitive action
- Order book: 10,000+ bids with zero offers = strong demand, likely price rise
- Price drop >3% in one day = rare on RSE, investigate before buying
- Bond coupon >13% = better than most equity yields, worth rotating cash
- New corporate announcement = re-price the company, check fundamentals
"""


def get_historical_context(conn, symbol, report_date, days=30):
    """Pull price trend for a stock over the last N days."""
    cur = conn.cursor()
    cur.execute("""
        SELECT report_date, closing_price, change_pct
        FROM equity_prices
        WHERE symbol = %s AND report_date < %s
        ORDER BY report_date DESC
        LIMIT %s
    """, (symbol, report_date, days))
    rows = cur.fetchall()
    cur.close()

    if not rows:
        return None

    prices = [r[1] for r in rows if r[1]]
    if not prices:
        return None

    return {
        "symbol": symbol,
        "days_of_data": len(rows),
        "current_price": prices[0],
        "price_30d_ago": prices[-1],
        "price_change_30d_pct": round(((prices[0] - prices[-1]) / prices[-1]) * 100, 2) if prices[-1] else None,
        "price_high_30d": max(prices),
        "price_low_30d": min(prices),
        "avg_price_30d": round(sum(prices) / len(prices), 2),
    }


def get_order_book_history(conn, symbol, report_date, days=14):
    """Show how order book has evolved — is demand building or fading?"""
    cur = conn.cursor()
    cur.execute("""
        SELECT report_date, side, SUM(quantity) as total
        FROM order_book
        WHERE symbol = %s AND report_date < %s
        GROUP BY report_date, side
        ORDER BY report_date DESC
        LIMIT %s
    """, (symbol, report_date, days * 2))
    rows = cur.fetchall()
    cur.close()

    bids_by_date = {}
    for r in rows:
        d = str(r[0])
        if d not in bids_by_date:
            bids_by_date[d] = {"bid": 0, "offer": 0}
        bids_by_date[d][r[1]] = r[2]

    if len(bids_by_date) < 2:
        return None

    dates = sorted(bids_by_date.keys(), reverse=True)
    latest_bids = bids_by_date[dates[0]]["bid"]
    oldest_bids = bids_by_date[dates[-1]]["bid"]

    return {
        "symbol": symbol,
        "current_bids": latest_bids,
        "trend": "building" if latest_bids > oldest_bids else "fading",
        "change_pct": round(((latest_bids - oldest_bids) / oldest_bids) * 100, 1) if oldest_bids else None,
    }


def get_dividend_history(conn, symbol):
    """Show past dividend payments to give context on yield."""
    cur = conn.cursor()
    cur.execute("""
        SELECT report_date, amount, record_date, payment_date
        FROM corporate_actions
        WHERE symbol = %s AND action_type = 'dividend'
        ORDER BY report_date DESC
        LIMIT 5
    """, (symbol,))
    rows = cur.fetchall()
    cur.close()
    return [{"date": str(r[0]), "amount": r[1], "record": str(r[2]) if r[2] else None} for r in rows]


def get_bond_context(conn, report_date):
    """Summarise bond market for yield comparison."""
    cur = conn.cursor()
    cur.execute("""
        SELECT AVG(coupon_rate), MAX(coupon_rate), MIN(coupon_rate), SUM(bond_turnover)
        FROM bond_trades WHERE report_date = %s AND coupon_rate IS NOT NULL
    """, (report_date,))
    row = cur.fetchone()
    cur.close()
    if not row or not row[0]:
        return None
    return {
        "avg_coupon": round(float(row[0]), 2),
        "max_coupon": float(row[1]),
        "min_coupon": float(row[2]),
        "total_turnover_rwf": int(row[3]) if row[3] else 0,
    }


def build_rich_context(conn, report_date, signals):
    """
    Assemble everything the model needs to write an accurate advisory.
    Returns a structured string ready to inject into the prompt.
    """
    lines = []

    # 1. RSE institutional knowledge
    lines.append(RSE_KNOWLEDGE)
    lines.append("=" * 60)
    lines.append(f"TODAY'S DATE: {report_date}")
    lines.append("")

    # 2. For each signal, pull 30-day historical context
    signal_symbols = list({s.get("symbol") for s in signals if s.get("symbol")})

    if signal_symbols:
        lines.append("HISTORICAL PRICE CONTEXT (last 30 days from our database):")
        for sym in signal_symbols:
            hist = get_historical_context(conn, sym, report_date)
            if hist:
                lines.append(
                    f"  {sym}: current {hist['current_price']} RWF | "
                    f"30d range {hist['price_low_30d']}–{hist['price_high_30d']} | "
                    f"30d change {hist['price_change_30d_pct']:+.1f}%"
                )
            ob = get_order_book_history(conn, sym, report_date)
            if ob:
                lines.append(
                    f"  {sym} order book trend: {ob['trend']} "
                    f"({'%+.0f' % ob['change_pct']}% over 2 weeks)" if ob['change_pct'] else f"  {sym} order book: {ob['trend']}"
                )
            div = get_dividend_history(conn, sym)
            if div:
                lines.append(f"  {sym} dividend history: " + ", ".join(
                    f"RWF {d['amount']} ({d['date']})" for d in div
                ))
        lines.append("")

    # 3. Bond market context
    bond_ctx = get_bond_context(conn, report_date)
    if bond_ctx:
        lines.append(f"BOND MARKET: avg coupon {bond_ctx['avg_coupon']}% | "
                     f"range {bond_ctx['min_coupon']}–{bond_ctx['max_coupon']}% | "
                     f"turnover {bond_ctx['total_turnover_rwf']:,} RWF")
        lines.append("")

    # 4. Today's signals (deduplicated)
    seen = set()
    unique_signals = []
    for s in signals:
        key = (s["type"], s.get("symbol"))
        if key not in seen:
            seen.add(key)
            unique_signals.append(s)

    lines.append(f"TODAY'S SIGNALS ({len(unique_signals)} unique):")
    for s in unique_signals:
        lines.append(f"  [{s['type'].upper()}] {s.get('symbol', 'MARKET')}: {s['message']}")
    lines.append("")

    return "\n".join(lines), unique_signals
