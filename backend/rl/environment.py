"""
RL Environment — defines State, Action space, and Reward function.

State:  A snapshot of market conditions at the time a signal fires.
        We encode it as a feature vector so the agent can distinguish
        "squeeze signal when market is active" from "squeeze when dead quiet".

Action: For each signal type, the agent chooses an emphasis level:
        0 = suppress (don't include in advisory)
        1 = mention briefly
        2 = highlight as primary recommendation

Reward: Measured 7 days later:
        +1.0  if price moved in predicted direction by >2%
        +0.3  if price moved in predicted direction by 0.5–2%
         0.0  if price was flat (±0.5%)
        -0.3  if price moved opposite by 0.5–2%
        -1.0  if price moved strongly opposite (>2%)

        For bond/market signals (no directional price): reward based on
        whether the bond yield was genuinely higher than average equity yield.
"""

from dataclasses import dataclass
from typing import Optional
import math


# ── State ────────────────────────────────────────────────────────────────────

@dataclass
class MarketState:
    """
    Encodes market conditions at the time of a signal.
    All values normalised to [0, 1] so the Q-function can handle them uniformly.
    """
    # Market activity
    equity_turnover_norm: float      # 0 = zero trades, 1 = very active day
    bond_turnover_norm: float        # 0 = no bonds, 1 = high bond activity

    # Order book pressure (0 = no bids, 1 = extreme bid pressure)
    bid_pressure_norm: float

    # Price momentum (0 = strong downtrend, 0.5 = flat, 1 = strong uptrend)
    price_momentum_norm: float

    # FX stability (0 = RWF collapsing, 0.5 = stable, 1 = RWF strengthening)
    fx_stability_norm: float

    # Bond yield attractiveness vs equity (0 = bonds worse, 1 = bonds much better)
    bond_vs_equity_yield_norm: float

    # Time context
    days_to_record_date: float       # 0 = record date passed, 1 = >30 days away
                                     # 0.8 = within 7 days (urgent)

    def to_vector(self):
        return [
            self.equity_turnover_norm,
            self.bond_turnover_norm,
            self.bid_pressure_norm,
            self.price_momentum_norm,
            self.fx_stability_norm,
            self.bond_vs_equity_yield_norm,
            self.days_to_record_date,
        ]

    def to_bucket(self):
        """
        Discretise state into a hashable bucket for Q-table lookup.
        Each feature → low/medium/high (0/1/2).
        This keeps the state space manageable (~2000 possible states).
        """
        def bucket(v, thresholds=(0.33, 0.66)):
            if v <= thresholds[0]:
                return 0
            elif v <= thresholds[1]:
                return 1
            return 2

        return (
            bucket(self.equity_turnover_norm),
            bucket(self.bond_turnover_norm),
            bucket(self.bid_pressure_norm),
            bucket(self.price_momentum_norm),
            bucket(self.fx_stability_norm),
            bucket(self.bond_vs_equity_yield_norm),
            bucket(self.days_to_record_date),
        )


def build_state(conn, report_date, symbol=None):
    """Build a MarketState from the database for a given date."""
    cur = conn.cursor()

    # Equity turnover (normalised against historical max)
    cur.execute("""
        SELECT COALESCE(SUM(e.closing_price * e.volume_traded), 0)
        FROM equity_prices e WHERE e.report_date = %s AND e.volume_traded > 0
    """, (report_date,))
    eq_turnover = float(cur.fetchone()[0] or 0)

    cur.execute("""
        SELECT COALESCE(MAX(sub.daily_total), 1)
        FROM (
            SELECT report_date, SUM(closing_price * volume_traded) as daily_total
            FROM equity_prices WHERE volume_traded > 0
            GROUP BY report_date
        ) sub
    """)
    max_eq = float(cur.fetchone()[0] or 1)
    equity_turnover_norm = min(eq_turnover / max_eq, 1.0)

    # Bond turnover
    cur.execute("SELECT COALESCE(SUM(bond_turnover), 0) FROM bond_trades WHERE report_date = %s", (report_date,))
    bond_turnover = float(cur.fetchone()[0] or 0)
    bond_turnover_norm = min(bond_turnover / 2_000_000_000, 1.0)  # 2B RWF as reference max

    # Order book bid pressure for this symbol (or market-wide)
    if symbol:
        cur.execute("""
            SELECT COALESCE(SUM(quantity), 0) FROM order_book
            WHERE report_date = %s AND symbol = %s AND side = 'bid'
        """, (report_date, symbol))
    else:
        cur.execute("""
            SELECT COALESCE(SUM(quantity), 0) FROM order_book
            WHERE report_date = %s AND side = 'bid'
        """, (report_date,))
    bids = float(cur.fetchone()[0] or 0)
    bid_pressure_norm = min(bids / 100_000, 1.0)  # 100K shares as reference max

    # Price momentum: compare today's close to 10-day avg
    if symbol:
        cur.execute("""
            SELECT closing_price FROM equity_prices
            WHERE symbol = %s AND report_date <= %s AND closing_price IS NOT NULL
            ORDER BY report_date DESC LIMIT 10
        """, (symbol, report_date))
        prices = [float(r[0]) for r in cur.fetchall()]
        if len(prices) >= 2:
            momentum = (prices[0] - prices[-1]) / prices[-1]
            price_momentum_norm = max(0.0, min(1.0, 0.5 + momentum * 5))
        else:
            price_momentum_norm = 0.5
    else:
        price_momentum_norm = 0.5

    # FX stability: USD/RWF change over last 5 days
    cur.execute("""
        SELECT buy_rate FROM fx_rates
        WHERE currency = 'USD' AND report_date <= %s
        ORDER BY report_date DESC LIMIT 5
    """, (report_date,))
    fx_rows = [float(r[0]) for r in cur.fetchall() if r[0]]
    if len(fx_rows) >= 2:
        fx_change = (fx_rows[0] - fx_rows[-1]) / fx_rows[-1]
        fx_stability_norm = max(0.0, min(1.0, 0.5 - fx_change * 10))
    else:
        fx_stability_norm = 0.5

    # Bond vs equity yield
    cur.execute("SELECT AVG(coupon_rate) FROM bond_trades WHERE report_date = %s AND coupon_rate > 0", (report_date,))
    bond_yield = float(cur.fetchone()[0] or 0)

    # Estimate equity yield from dividend / price (rough)
    cur.execute("""
        SELECT AVG(ca.amount / NULLIF(ep.closing_price, 0))
        FROM corporate_actions ca
        JOIN equity_prices ep ON ca.symbol = ep.symbol AND ep.report_date = %s
        WHERE ca.action_type = 'dividend' AND ca.amount > 0
    """, (report_date,))
    row = cur.fetchone()
    equity_yield = float(row[0] or 0) * 100 if row and row[0] else 8.0  # default 8% if unknown

    if bond_yield > 0:
        bond_vs_equity_yield_norm = min(bond_yield / (bond_yield + equity_yield), 1.0)
    else:
        bond_vs_equity_yield_norm = 0.5

    # Days to nearest record date
    cur.execute("""
        SELECT MIN(record_date - %s)
        FROM corporate_actions
        WHERE record_date >= %s AND action_type = 'dividend'
    """, (report_date, report_date))
    row = cur.fetchone()
    days_to_record = int(row[0]) if row and row[0] is not None else 999
    days_to_record_date = max(0.0, min(1.0, 1.0 - (days_to_record / 30.0)))

    cur.close()

    return MarketState(
        equity_turnover_norm=equity_turnover_norm,
        bond_turnover_norm=bond_turnover_norm,
        bid_pressure_norm=bid_pressure_norm,
        price_momentum_norm=price_momentum_norm,
        fx_stability_norm=fx_stability_norm,
        bond_vs_equity_yield_norm=bond_vs_equity_yield_norm,
        days_to_record_date=days_to_record_date,
    )


# ── Reward ────────────────────────────────────────────────────────────────────

def compute_reward(predicted_direction: str, price_then: float, price_later: float) -> float:
    """
    Compare prediction to actual outcome.
    Returns reward in [-1.0, +1.0].
    """
    if not price_then or not price_later or price_then == 0:
        return 0.0

    change_pct = ((price_later - price_then) / price_then) * 100

    if predicted_direction == "up":
        if change_pct > 2.0:   return +1.0
        if change_pct > 0.5:   return +0.3
        if change_pct > -0.5:  return  0.0
        if change_pct > -2.0:  return -0.3
        return -1.0

    elif predicted_direction == "down":
        if change_pct < -2.0:  return +1.0
        if change_pct < -0.5:  return +0.3
        if change_pct < 0.5:   return  0.0
        if change_pct < 2.0:   return -0.3
        return -1.0

    return 0.0  # neutral prediction — no reward/penalty
