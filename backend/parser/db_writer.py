"""
Takes parsed PDF data and writes it to the database.
"""
import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import get_conn


def write_report(data):
    conn = get_conn()
    cur = conn.cursor()
    report_date = data["report_date"]
    inserted = {"equity": 0, "fx": 0, "bonds": 0, "order_book": 0, "corporate_actions": 0}

    for row in data["equity"]:
        try:
            cur.execute("""
                INSERT INTO equity_prices (report_date, symbol, name, volume_traded, closing_price, previous_price, change_pct)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (report_date, symbol) DO NOTHING
            """, (report_date, row["symbol"], row["name"], row["volume_traded"],
                  row["closing_price"], row["previous_price"], row["change_pct"]))
            inserted["equity"] += cur.rowcount
        except Exception as e:
            print(f"  Equity insert error: {e}")

    for row in data["fx"]:
        try:
            cur.execute("""
                INSERT INTO fx_rates (report_date, currency, buy_rate, sell_rate)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (report_date, currency) DO NOTHING
            """, (report_date, row["currency"], row["buy_rate"], row["sell_rate"]))
            inserted["fx"] += cur.rowcount
        except Exception as e:
            print(f"  FX insert error: {e}")

    for row in data["bonds"]:
        try:
            cur.execute("""
                INSERT INTO bond_trades (report_date, symbol, volume_traded, current_price, coupon_rate, bond_turnover, years_to_maturity)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (report_date, symbol) DO NOTHING
            """, (report_date, row["symbol"], row["volume_traded"], row["current_price"],
                  row["coupon_rate"], row["bond_turnover"], row["years_to_maturity"]))
            inserted["bonds"] += cur.rowcount
        except Exception as e:
            print(f"  Bond insert error: {e}")

    for row in data["order_book"]:
        try:
            cur.execute("""
                INSERT INTO order_book (report_date, symbol, side, quantity, price)
                VALUES (%s, %s, %s, %s, %s)
            """, (report_date, row["symbol"], row["side"], row["quantity"], row["price"]))
            inserted["order_book"] += cur.rowcount
        except Exception as e:
            print(f"  Order book insert error: {e}")

    for row in data["corporate_actions"]:
        try:
            cur.execute("""
                INSERT INTO corporate_actions (report_date, symbol, action_type, description, amount, record_date, payment_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (report_date, row["symbol"], row["action_type"], row["description"],
                  row["amount"], row["record_date"], row["payment_date"]))
            inserted["corporate_actions"] += cur.rowcount
        except Exception as e:
            print(f"  Corporate action insert error: {e}")

    conn.commit()
    cur.close()
    conn.close()
    return inserted
