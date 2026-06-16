"""
Parses trade confirmation / contract note PDFs from MO Capital.
These confirm a buy or sell you executed.
"""
import re
from datetime import datetime


def clean_number(val):
    if val is None:
        return None
    val = str(val).strip().replace(",", "").replace("RWF", "").replace("Rwf", "").strip()
    try:
        return float(val) if val else None
    except ValueError:
        return None


def parse_trade_confirmation(text):
    """
    Extracts from contract note text:
    - trade_date
    - symbol
    - side (buy/sell)
    - quantity
    - price_per_share
    - total_consideration
    - brokerage_fee
    - settlement_date
    """
    result = {
        "doc_type": "trade_confirmation",
        "trade_date": None,
        "symbol": None,
        "side": None,
        "quantity": None,
        "price_per_share": None,
        "total_consideration": None,
        "brokerage_fee": None,
        "settlement_date": None,
        "raw_text": text[:500],
    }

    tl = text.lower()

    # Side
    if "bought" in tl or "purchase" in tl or "buy" in tl:
        result["side"] = "buy"
    elif "sold" in tl or "sale" in tl or "sell" in tl:
        result["side"] = "sell"

    # Known stock symbols
    symbols = ["BOK", "BLR", "MTNR", "IMR", "CMR", "EQTY", "KCB", "USL", "BK"]
    for sym in symbols:
        if sym.lower() in tl or sym in text:
            result["symbol"] = sym
            break

    # Quantity
    m = re.search(r"(?:number of shares|quantity|shares)[:\s]+([\d,]+)", tl)
    if m:
        result["quantity"] = int(clean_number(m.group(1)))

    # Price per share
    m = re.search(r"(?:price per share|unit price|price)[:\s]+(?:rwf|frw)?\s*([\d,\.]+)", tl)
    if m:
        result["price_per_share"] = clean_number(m.group(1))

    # Total consideration
    m = re.search(r"(?:total consideration|gross amount|total amount)[:\s]+(?:rwf|frw)?\s*([\d,\.]+)", tl)
    if m:
        result["total_consideration"] = clean_number(m.group(1))

    # Brokerage / commission
    m = re.search(r"(?:brokerage|commission|fee)[:\s]+(?:rwf|frw)?\s*([\d,\.]+)", tl)
    if m:
        result["brokerage_fee"] = clean_number(m.group(1))

    # Dates
    date_pattern = r"(\d{1,2})[/\-\s](\w+)[/\-\s](\d{4})"
    dates = re.findall(date_pattern, text)
    for d in dates:
        try:
            dt = datetime.strptime(f"{d[0]} {d[1]} {d[2]}", "%d %B %Y").date()
            if result["trade_date"] is None:
                result["trade_date"] = str(dt)
            else:
                result["settlement_date"] = str(dt)
        except Exception:
            pass

    return result
