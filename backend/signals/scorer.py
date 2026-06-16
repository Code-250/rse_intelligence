"""
Signal scorer — decides whether today's signals are worth sending to the investor.

Logic:
  1. Load signal weights from DB (adjusted by RL over time)
  2. Score today's signals
  3. Return True (send advisory) or False (stay silent)

Reinforcement learning:
  - Every signal that gets sent, we record a predicted outcome
  - 7 days later, we check the actual price movement
  - If we were right → increase signal weight
  - If we were wrong → decrease signal weight
  - Over time, signals that consistently lead to correct predictions get weighted higher
  - Signals that are noisy get suppressed
"""

from db import get_conn
from datetime import date, timedelta


def load_weights(conn):
    """Load current signal weights from DB."""
    cur = conn.cursor()
    cur.execute("SELECT signal_type, weight, send_threshold, accuracy_rate FROM signal_weights")
    weights = {row[0]: {"weight": row[1], "threshold": row[2], "accuracy": row[3]}
               for row in cur.fetchall()}
    cur.close()
    return weights


def score_signals(signals, weights):
    """
    Score a list of signals. Returns (total_score, breakdown).
    Each signal contributes its type's weight if it passes basic quality checks.
    """
    breakdown = []
    total = 0.0

    seen_types = {}  # deduplicate by type+symbol
    for s in signals:
        key = (s["type"], s.get("symbol"))
        if key in seen_types:
            continue
        seen_types[key] = True

        w = weights.get(s["type"], {"weight": 1.0, "threshold": 0.5, "accuracy": 0.5})

        # Bonus: dividend capture with record date ≤ 3 days is always urgent
        bonus = 0.0
        if s["type"] == "dividend_capture":
            bonus = 1.5  # always push this through

        signal_score = float(w["weight"]) + bonus
        total += signal_score
        breakdown.append({
            "type": s["type"],
            "symbol": s.get("symbol"),
            "score": signal_score,
            "weight": w["weight"],
            "accuracy": w["accuracy"],
        })

    return total, breakdown


def should_send(signals, conn, min_score=1.5):
    """
    Main decision: should we send an advisory today?

    Returns (bool, reason_string, scored_signals)
    """
    if not signals:
        return False, "No signals triggered today. Market is quiet.", []

    weights = load_weights(conn)
    total_score, breakdown = score_signals(signals, weights)

    # Always send if there's a dividend capture (time-sensitive, can't miss)
    has_urgent = any(s["type"] == "dividend_capture" for s in signals)
    if has_urgent:
        return True, f"Urgent: dividend capture signal present (score={total_score:.1f})", breakdown

    if total_score >= min_score:
        return True, f"Score {total_score:.1f} >= threshold {min_score}", breakdown

    return False, f"Score {total_score:.1f} below threshold {min_score} — not worth messaging today", breakdown


# ── RL: record predictions ────────────────────────────────────────────────────

SIGNAL_PREDICTIONS = {
    "dividend_capture": "up",    # buying before record date → price usually holds or rises
    "squeeze":          "up",    # bids >> offers → price rises when sellers appear
    "price_drop":       "up",    # contrarian — dropped stock recovers
    "bond_yield":       "neutral", # bond signal, not price-directional
}


def record_predictions(conn, signals, report_date):
    """
    After sending an advisory, record what each signal predicted.
    We'll check outcomes 7 days later.
    """
    cur = conn.cursor()
    for s in signals:
        predicted = SIGNAL_PREDICTIONS.get(s["type"], "neutral")

        # Get current price for comparison later
        price = None
        if s.get("symbol"):
            cur.execute("""
                SELECT closing_price FROM equity_prices
                WHERE symbol = %s AND report_date = %s
            """, (s["symbol"], report_date))
            row = cur.fetchone()
            price = row[0] if row else None

        # Get signal DB id
        cur.execute("""
            SELECT id FROM signals
            WHERE signal_date = %s AND signal_type = %s AND symbol IS NOT DISTINCT FROM %s
            LIMIT 1
        """, (report_date, s["type"], s.get("symbol")))
        row = cur.fetchone()
        signal_id = row[0] if row else None

        cur.execute("""
            INSERT INTO signal_outcomes
                (signal_id, signal_date, signal_type, symbol, predicted_direction, price_at_signal)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (signal_id, report_date, s["type"], s.get("symbol"), predicted, price))

    conn.commit()
    cur.close()


# ── RL: check outcomes and update weights ─────────────────────────────────────

def update_weights_from_outcomes(conn):
    """
    Run daily. For any outcomes where 7 days have passed:
    - Compare predicted direction to actual price movement
    - Mark correct/incorrect
    - Update signal_weights table (accuracy_rate + weight)
    """
    check_before = date.today() - timedelta(days=7)
    cur = conn.cursor()

    # Find unchecked outcomes old enough to evaluate
    cur.execute("""
        SELECT id, signal_type, symbol, predicted_direction, price_at_signal, signal_date
        FROM signal_outcomes
        WHERE outcome_checked = FALSE AND signal_date <= %s
    """, (check_before,))
    outcomes = cur.fetchall()

    if not outcomes:
        print("  No outcomes ready to evaluate yet.")
        cur.close()
        return

    print(f"  Evaluating {len(outcomes)} signal outcomes...")

    for outcome in outcomes:
        oid, sig_type, symbol, predicted, price_then, sig_date = outcome

        if not symbol or not price_then:
            # Can't evaluate bond/market-wide signals by price
            cur.execute("UPDATE signal_outcomes SET outcome_checked = TRUE WHERE id = %s", (oid,))
            continue

        # Get price 7 days after signal
        cur.execute("""
            SELECT closing_price FROM equity_prices
            WHERE symbol = %s AND report_date > %s
            ORDER BY report_date ASC LIMIT 1
        """, (symbol, sig_date))
        row = cur.fetchone()

        if not row or not row[0]:
            continue  # price data not available yet

        price_now = row[0]
        change_pct = ((price_now - price_then) / price_then) * 100

        if change_pct > 1.5:
            actual = "up"
        elif change_pct < -1.5:
            actual = "down"
        else:
            actual = "neutral"

        was_correct = (predicted == actual)

        cur.execute("""
            UPDATE signal_outcomes
            SET price_7d_later = %s, actual_direction = %s,
                was_correct = %s, outcome_checked = TRUE
            WHERE id = %s
        """, (price_now, actual, was_correct, oid))

        # Update running accuracy and weight for this signal type
        cur.execute("""
            UPDATE signal_weights
            SET times_triggered = times_triggered + 1,
                times_correct   = times_correct + %s,
                accuracy_rate   = (times_correct + %s)::NUMERIC / (times_triggered + 1),
                weight = CASE
                    WHEN (times_correct + %s)::NUMERIC / (times_triggered + 1) >= 0.65
                        THEN LEAST(weight * 1.1, 3.0)   -- good signal, boost up to max 3.0
                    WHEN (times_correct + %s)::NUMERIC / (times_triggered + 1) <= 0.35
                        THEN GREATEST(weight * 0.9, 0.3) -- bad signal, reduce to min 0.3
                    ELSE weight                           -- neutral, no change
                END,
                updated_at = NOW()
            WHERE signal_type = %s
        """, (1 if was_correct else 0,
              1 if was_correct else 0,
              1 if was_correct else 0,
              1 if was_correct else 0,
              sig_type))

        direction_str = "✓" if was_correct else "✗"
        print(f"    {direction_str} {sig_type} {symbol}: predicted {predicted}, actual {actual} "
              f"({change_pct:+.1f}%)")

    conn.commit()
    cur.close()
    print("  Weights updated.")
