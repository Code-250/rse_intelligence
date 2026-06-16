"""
Parses MO Capital daily market report PDFs.
"""
import pdfplumber
import re
import os
from datetime import datetime, date


def extract_date_from_text(text):
    # Handles "28thMay 2026" or "28th May 2026" or "28 May 2026"
    m = re.search(
        r"(\d{1,2})(?:st|nd|rd|th)?\s*(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
        text, re.IGNORECASE
    )
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y").date()
        except Exception:
            pass
    return date.today()


def clean_number(val):
    if val is None:
        return None
    val = str(val).strip().replace(",", "").replace("%", "").replace(" ", "")
    # Remove spaces inside numbers like "5 6,500,000"
    try:
        return float(val) if val and val not in ("-", "") else None
    except ValueError:
        return None


def parse_equity_table(tables):
    """
    Equity rows: Name | Symbol | (empty) | Closing | Previous | Turnover | Change%
    """
    equity_symbols = {"BoK", "BLR", "MTNR", "IMR", "CMR", "EQTY", "KCB", "USL"}
    rows = []
    for table in tables:
        for row in table:
            if not row or len(row) < 4:
                continue
            cells = [str(c).strip() if c else "" for c in row]
            # Symbol is in column 1
            sym = cells[1] if len(cells) > 1 else ""
            if sym not in equity_symbols:
                continue
            rows.append({
                "symbol": sym,
                "name": cells[0],
                "volume_traded": None,
                "closing_price": clean_number(cells[3]) if len(cells) > 3 else None,
                "previous_price": clean_number(cells[4]) if len(cells) > 4 else None,
                "change_pct": clean_number(cells[6]) if len(cells) > 6 else None,
            })
    return rows


def parse_fx_table(text):
    """Parse FX from raw text since tables merge columns."""
    currencies = ["USD", "EUR", "GBP", "ZAR", "KES", "TZS"]
    rows = []
    for cur in currencies:
        # Match: USD 1456.38 1466.38
        m = re.search(rf"{cur}\s+([\d,\.]+)\s+([\d,\.]+)", text)
        if m:
            rows.append({
                "currency": cur,
                "buy_rate": clean_number(m.group(1)),
                "sell_rate": clean_number(m.group(2)),
            })
    return rows


def parse_bond_table(tables):
    rows = []
    for table in tables:
        for row in table:
            if not row or len(row) < 4:
                continue
            cells = [str(c).strip() if c else "" for c in row]
            if cells[1] != "TBOND":
                continue
            rows.append({
                "symbol": cells[0],
                "volume_traded": int(clean_number(cells[2])) if clean_number(cells[2]) else None,
                "current_price": clean_number(cells[3]),
                "coupon_rate": clean_number(cells[4]),
                "bond_turnover": int(clean_number(cells[5])) if clean_number(cells[5]) else None,
                "years_to_maturity": clean_number(cells[6]) if len(cells) > 6 else None,
            })
    return rows


def parse_order_book(tables):
    rows = []
    order_section = False
    for table in tables:
        for row in table:
            if not row:
                continue
            cells = [str(c).strip() if c else "" for c in row]
            if "Outstanding Bids" in " ".join(cells):
                order_section = True
                continue
            if not order_section:
                continue
            if cells[0] in ("Stock", "Totals", ""):
                continue
            # Parse: Name | Symbol | Bid Qty | Bid Price | None | Offer Qty | Offer Price
            # Some rows have symbol embedded in name like "KCB KCB"
            name = cells[0]
            sym = cells[1] if cells[1] else name.split()[-1] if name else ""
            sym = sym.upper()

            bid_qty = clean_number(cells[2]) if len(cells) > 2 else None
            bid_price = clean_number(cells[3]) if len(cells) > 3 else None
            offer_qty = clean_number(cells[5]) if len(cells) > 5 else None
            offer_price = clean_number(cells[6]) if len(cells) > 6 else None

            if bid_qty:
                rows.append({"symbol": sym, "side": "bid", "quantity": int(bid_qty), "price": bid_price})
            if offer_qty:
                rows.append({"symbol": sym, "side": "offer", "quantity": int(offer_qty), "price": offer_price})
    return rows


def parse_corporate_actions(text, report_date):
    actions = []

    # I&M dividend
    m = re.search(r"I&M Bank.*?FRW\s*([\d,\.]+)\s*dividend per share", text, re.IGNORECASE | re.DOTALL)
    if m:
        actions.append({
            "symbol": "IMR", "action_type": "dividend",
            "description": "I&M Bank dividend per share",
            "amount": clean_number(m.group(1)),
            "record_date": None, "payment_date": None,
        })

    # BK Group dividend
    m = re.search(r"RWF\s*([\d,\.]+)\s*per share.*?BK Group", text, re.IGNORECASE | re.DOTALL)
    if m:
        actions.append({
            "symbol": "BOK", "action_type": "dividend",
            "description": "BK Group dividend per share",
            "amount": clean_number(m.group(1)),
            "record_date": None, "payment_date": None,
        })

    # BRALIRWA dividend + record date
    m = re.search(r"BRALIRWA.*?Rwf\s*([\d,\.]+)\s*per share", text, re.IGNORECASE | re.DOTALL)
    if m:
        # Record date: "shareholders by May 31, 2026"
        rec = re.search(r"shareholders by\s+(\w+ \d+,\s*\d+)", text, re.IGNORECASE)
        record_date = None
        if rec:
            try:
                record_date = datetime.strptime(rec.group(1).replace(",", "").strip(), "%B %d %Y").date()
            except Exception:
                pass
        pay = re.search(r"paid on\s+(\w+ \d+,\s*\d+)", text, re.IGNORECASE)
        payment_date = None
        if pay:
            try:
                payment_date = datetime.strptime(pay.group(1).replace(",", "").strip(), "%B %d %Y").date()
            except Exception:
                pass
        actions.append({
            "symbol": "BLR", "action_type": "dividend",
            "description": "BRALIRWA dividend per share",
            "amount": clean_number(m.group(1)),
            "record_date": record_date,
            "payment_date": payment_date,
        })

    return actions


def parse_pdf(filepath):
    result = {
        "report_date": None,
        "equity": [],
        "fx": [],
        "bonds": [],
        "order_book": [],
        "corporate_actions": [],
    }

    with pdfplumber.open(filepath) as pdf:
        full_text = ""
        all_tables = []
        for page in pdf.pages:
            text = page.extract_text() or ""
            full_text += text + "\n"
            all_tables.extend(page.extract_tables())

    result["report_date"] = extract_date_from_text(full_text)
    result["equity"] = parse_equity_table(all_tables)
    result["fx"] = parse_fx_table(full_text)
    result["bonds"] = parse_bond_table(all_tables)
    result["order_book"] = parse_order_book(all_tables)
    result["corporate_actions"] = parse_corporate_actions(full_text, result["report_date"])

    return result


if __name__ == "__main__":
    import sys, json
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        data = parse_pdf(path)
        print(json.dumps(data, indent=2, default=str))
