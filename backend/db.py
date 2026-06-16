import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS equity_prices (
            id SERIAL PRIMARY KEY,
            report_date DATE NOT NULL,
            symbol VARCHAR(10) NOT NULL,
            name VARCHAR(100),
            volume_traded INTEGER,
            closing_price NUMERIC,
            previous_price NUMERIC,
            change_pct NUMERIC,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(report_date, symbol)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS fx_rates (
            id SERIAL PRIMARY KEY,
            report_date DATE NOT NULL,
            currency VARCHAR(10) NOT NULL,
            buy_rate NUMERIC,
            sell_rate NUMERIC,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(report_date, currency)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bond_trades (
            id SERIAL PRIMARY KEY,
            report_date DATE NOT NULL,
            symbol VARCHAR(50) NOT NULL,
            volume_traded BIGINT,
            current_price NUMERIC,
            coupon_rate NUMERIC,
            bond_turnover BIGINT,
            years_to_maturity NUMERIC,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(report_date, symbol)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS order_book (
            id SERIAL PRIMARY KEY,
            report_date DATE NOT NULL,
            symbol VARCHAR(10) NOT NULL,
            side VARCHAR(5) NOT NULL,  -- 'bid' or 'offer'
            quantity INTEGER,
            price NUMERIC,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS corporate_actions (
            id SERIAL PRIMARY KEY,
            report_date DATE NOT NULL,
            symbol VARCHAR(10),
            action_type VARCHAR(50),  -- 'dividend', 'agm', 'rights'
            description TEXT,
            amount NUMERIC,
            record_date DATE,
            payment_date DATE,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id SERIAL PRIMARY KEY,
            signal_date DATE NOT NULL,
            signal_type VARCHAR(50) NOT NULL,
            symbol VARCHAR(10),
            message TEXT NOT NULL,
            alerted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(signal_date, signal_type, symbol)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS my_trades (
            id SERIAL PRIMARY KEY,
            trade_date DATE,
            symbol VARCHAR(10),
            side VARCHAR(5),
            quantity INTEGER,
            price_per_share NUMERIC,
            total_consideration NUMERIC,
            brokerage_fee NUMERIC,
            settlement_date DATE,
            source_file VARCHAR(255),
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS my_holdings (
            id SERIAL PRIMARY KEY,
            statement_date DATE,
            symbol VARCHAR(10),
            quantity INTEGER,
            current_value NUMERIC,
            cash_balance NUMERIC,
            total_portfolio_value NUMERIC,
            source_file VARCHAR(255),
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS raw_documents (
            id SERIAL PRIMARY KEY,
            filename VARCHAR(255) UNIQUE,
            doc_type VARCHAR(50),
            email_subject TEXT,
            email_date TEXT,
            parsed BOOLEAN DEFAULT FALSE,
            parse_error TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("Database initialized.")

if __name__ == "__main__":
    init_db()
