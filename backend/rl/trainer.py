"""
RL Trainer — runs daily to:
1. Find signal outcomes that are now 7 days old and ready to evaluate
2. Compute rewards from actual price movements
3. Update the Q-agent
4. Adjust signal emphasis for future advisories

Also provides: replay training on historical data
(lets the agent learn from all past data at startup, not just day by day)
"""

import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, timedelta
from db import get_conn
from rl.environment import build_state, compute_reward
from rl.q_agent import QAgent


def run_daily_training(conn=None):
    """
    Called every morning. Evaluates signals from 7 days ago, updates Q-table.
    """
    close_conn = conn is None
    if close_conn:
        conn = get_conn()

    agent = QAgent(conn)
    check_before = date.today() - timedelta(days=7)

    cur = conn.cursor()
    cur.execute("""
        SELECT id, signal_type, symbol, predicted_direction,
               price_at_signal, signal_date, action_taken
        FROM signal_outcomes
        WHERE outcome_checked = FALSE
          AND signal_date <= %s
    """, (check_before,))
    outcomes = cur.fetchall()
    cur.close()

    if not outcomes:
        print("  No outcomes ready for RL training today.")
        agent.end_episode()
        if close_conn:
            conn.close()
        return

    print(f"  Training on {len(outcomes)} signal outcomes...")
    updates = 0

    for row in outcomes:
        oid, sig_type, symbol, predicted, price_then, sig_date, action_taken = row

        # Get price 7 days after signal
        cur = conn.cursor()
        cur.execute("""
            SELECT closing_price FROM equity_prices
            WHERE symbol = %s AND report_date > %s
            ORDER BY report_date ASC LIMIT 1
        """, (symbol, sig_date))
        price_row = cur.fetchone()
        cur.close()

        if not price_row or not price_row[0]:
            continue

        price_later = float(price_row[0])
        price_then_f = float(price_then) if price_then else None

        # Compute reward
        reward = compute_reward(predicted or "neutral", price_then_f, price_later)

        # Build state at time of signal and current state
        state      = build_state(conn, sig_date, symbol)
        next_state = build_state(conn, date.today(), symbol)

        # Default action to 1 (include) if not recorded
        action = int(action_taken) if action_taken is not None else 1

        # Update Q-table
        agent.learn(state, sig_type, action, reward, next_state)

        # Mark outcome as checked
        change_pct = ((price_later - price_then_f) / price_then_f * 100) if price_then_f else 0
        actual = "up" if change_pct > 1.5 else "down" if change_pct < -1.5 else "neutral"
        was_correct = (predicted == actual)

        cur = conn.cursor()
        cur.execute("""
            UPDATE signal_outcomes
            SET price_7d_later = %s, actual_direction = %s,
                was_correct = %s, outcome_checked = TRUE
            WHERE id = %s
        """, (price_later, actual, was_correct, oid))
        conn.commit()
        cur.close()

        direction = "✓" if was_correct else "✗"
        print(f"    {direction} {sig_type} {symbol or ''}: "
              f"predicted={predicted} actual={actual} "
              f"reward={reward:+.1f}")
        updates += 1

    agent.end_episode()

    # Update signal_weights table from Q-table knowledge
    _sync_weights_from_q_table(agent, conn)

    print(f"  RL training complete. {updates} outcomes processed.")

    if close_conn:
        conn.close()


def _sync_weights_from_q_table(agent: QAgent, conn):
    """
    Convert Q-table knowledge into signal_weights for the scorer.
    The scorer still uses signal_weights — the Q-table informs them.
    """
    from rl.q_agent import SIGNAL_TYPES, ACTIONS

    cur = conn.cursor()
    for sig_type in SIGNAL_TYPES:
        # Average Q-value for "highlight" action across all states
        highlight_q_vals = []
        suppress_q_vals  = []

        for state_key, signals in agent.q_table.items():
            if sig_type in signals:
                highlight_q_vals.append(signals[sig_type].get(2, 0.0))
                suppress_q_vals.append(signals[sig_type].get(0, 0.0))

        if not highlight_q_vals:
            continue

        avg_highlight = sum(highlight_q_vals) / len(highlight_q_vals)
        avg_suppress  = sum(suppress_q_vals)  / len(suppress_q_vals) if suppress_q_vals else 0

        # Convert Q-values to a weight [0.3 – 3.0]
        # If highlight Q >> suppress Q → high weight
        q_diff = avg_highlight - avg_suppress
        new_weight = max(0.3, min(3.0, 1.0 + q_diff))

        cur.execute("""
            UPDATE signal_weights
            SET weight = %s, updated_at = NOW()
            WHERE signal_type = %s
        """, (new_weight, sig_type))
        print(f"    Updated weight for {sig_type}: {new_weight:.3f}")

    conn.commit()
    cur.close()


def replay_historical_training(conn=None):
    """
    Train the agent on ALL historical outcomes at once.
    Run this once to bootstrap the agent from existing data.
    Useful after a DB reset or when adding the RL system to an existing dataset.
    """
    close_conn = conn is None
    if close_conn:
        conn = get_conn()

    print("Running historical RL replay training...")
    agent = QAgent(conn)

    cur = conn.cursor()
    cur.execute("""
        SELECT id, signal_type, symbol, predicted_direction,
               price_at_signal, signal_date, action_taken,
               price_7d_later, actual_direction
        FROM signal_outcomes
        WHERE outcome_checked = TRUE
          AND price_7d_later IS NOT NULL
        ORDER BY signal_date ASC
    """)
    outcomes = cur.fetchall()
    cur.close()

    print(f"  Found {len(outcomes)} historical outcomes to train on.")
    if not outcomes:
        print("  No historical outcomes yet. Run daily for 7+ days first.")
        if close_conn:
            conn.close()
        return

    for row in outcomes:
        oid, sig_type, symbol, predicted, price_then, sig_date, action_taken, price_later, actual = row
        if not price_then or not price_later:
            continue

        reward = compute_reward(predicted or "neutral", float(price_then), float(price_later))
        state  = build_state(conn, sig_date, symbol)
        next_state = build_state(conn, date.today(), symbol)
        action = int(action_taken) if action_taken is not None else 1
        agent.learn(state, sig_type, action, reward, next_state)

    agent.end_episode()
    _sync_weights_from_q_table(agent, conn)
    agent.print_policy()

    print("Historical replay complete.")
    if close_conn:
        conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--replay":
        replay_historical_training()
    else:
        run_daily_training()
