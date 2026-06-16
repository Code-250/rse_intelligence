"""
Parses account statement PDFs from MO Capital.
Shows your portfolio holdings and cash balance.
"""
import re


def clean_number(val):
    if val is None:
        return None
    val = str(val).strip().replace(",", "").replace("RWF", "").replace("Rwf", "").strip()
    try:
        return float(val) if val else None
    except ValueError:
        return None


def parse_account_statement(text):
    """
    Extracts:
    - statement_date
    - holdings: [{symbol, quantity, avg_cost, current_value}]
    - cash_balance
    - total_portfolio_value
    """
    result = {
        "doc_type": "account_statement",
        "statement_date": None,
        "holdings": [],
        "cash_balance": None,
        "total_portfolio_value": None,
        "raw_text": text[:500],
    }

    tl = text.lower()
    symbols = ["BOK", "BLR", "MTNR", "IMR", "CMR", "EQTY", "KCB", "USL", "BK"]

    # Cash balance
    m = re.search(r"(?:cash balance|available cash|cash)[:\s]+(?:rwf|frw)?\s*([\d,\.]+)", tl)
    if m:
        result["cash_balance"] = clean_number(m.group(1))

    # Total portfolio
    m = re.search(r"(?:total portfolio|portfolio value|total value)[:\s]+(?:rwf|frw)?\s*([\d,\.]+)", tl)
    if m:
        result["total_portfolio_value"] = clean_number(m.group(1))

    # Holdings — look for symbol + numbers near it
    for sym in symbols:
        if sym.lower() in tl or sym in text:
            # Find numbers near the symbol mention
            pattern = rf"{sym}[\s\S]{{0,100}}?([\d,]+)\s+(?:shares|units)"
            m = re.search(pattern, text, re.IGNORECASE)
            qty = int(clean_number(m.group(1))) if m else None

            val_pattern = rf"{sym}[\s\S]{{0,200}}?(?:rwf|frw)?\s*([\d,\.]+)"
            mv = re.search(val_pattern, text, re.IGNORECASE)
            value = clean_number(mv.group(1)) if mv else None

            result["holdings"].append({
                "symbol": sym,
                "quantity": qty,
                "current_value": value,
            })

    return result
