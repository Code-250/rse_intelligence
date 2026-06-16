"""
Q-Learning Agent for RSE signal emphasis decisions.

The agent maintains a Q-table:
  Q[state_bucket][signal_type][action] = expected_reward

Actions per signal type:
  0 = suppress this signal (don't mention it)
  1 = include with normal weight
  2 = highlight as primary recommendation

The agent uses epsilon-greedy exploration:
  - With probability epsilon: try a random action (explore)
  - Otherwise: pick the action with highest expected reward (exploit)

Epsilon decays over time — early on we explore more,
later we exploit what we've learned.

The Q-table is persisted to the database so it survives restarts.
"""

import json
import random
import math
from datetime import date
from rl.environment import MarketState, build_state, compute_reward

SIGNAL_TYPES = ["dividend_capture", "squeeze", "bond_yield", "price_drop"]
ACTIONS = [0, 1, 2]  # suppress, include, highlight

# Hyperparameters
LEARNING_RATE   = 0.1    # how fast we update Q values (alpha)
DISCOUNT_FACTOR = 0.9    # how much we value future rewards (gamma)
EPSILON_START   = 0.3    # initial exploration rate
EPSILON_MIN     = 0.05   # minimum exploration rate
EPSILON_DECAY   = 0.995  # decay per episode (day)


class QAgent:
    def __init__(self, conn):
        self.conn = conn
        self.q_table = {}      # {state_bucket: {signal_type: {action: q_value}}}
        self.epsilon = EPSILON_START
        self.episode_count = 0
        self._load_from_db()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_from_db(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rl_q_table (
                id SERIAL PRIMARY KEY,
                state_key TEXT NOT NULL,
                signal_type VARCHAR(50) NOT NULL,
                action INTEGER NOT NULL,
                q_value NUMERIC DEFAULT 0.0,
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(state_key, signal_type, action)
            );
            CREATE TABLE IF NOT EXISTS rl_agent_state (
                id INTEGER PRIMARY KEY DEFAULT 1,
                epsilon NUMERIC DEFAULT 0.3,
                episode_count INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT NOW()
            );
        """)

        # Load Q-table
        cur.execute("SELECT state_key, signal_type, action, q_value FROM rl_q_table")
        for row in cur.fetchall():
            state_key, sig_type, action, q_val = row
            if state_key not in self.q_table:
                self.q_table[state_key] = {}
            if sig_type not in self.q_table[state_key]:
                self.q_table[state_key][sig_type] = {}
            self.q_table[state_key][sig_type][action] = float(q_val)

        # Load agent state (epsilon, episode count)
        cur.execute("SELECT epsilon, episode_count FROM rl_agent_state WHERE id = 1")
        row = cur.fetchone()
        if row:
            self.epsilon = float(row[0])
            self.episode_count = int(row[1])

        self.conn.commit()
        cur.close()

    def _save_to_db(self):
        cur = self.conn.cursor()

        for state_key, signals in self.q_table.items():
            for sig_type, actions in signals.items():
                for action, q_val in actions.items():
                    cur.execute("""
                        INSERT INTO rl_q_table (state_key, signal_type, action, q_value)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (state_key, signal_type, action)
                        DO UPDATE SET q_value = EXCLUDED.q_value, updated_at = NOW()
                    """, (state_key, sig_type, action, q_val))

        cur.execute("""
            INSERT INTO rl_agent_state (id, epsilon, episode_count)
            VALUES (1, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET epsilon = EXCLUDED.epsilon,
                episode_count = EXCLUDED.episode_count,
                updated_at = NOW()
        """, (self.epsilon, self.episode_count))

        self.conn.commit()
        cur.close()

    # ── Q-value helpers ───────────────────────────────────────────────────────

    def _get_q(self, state_key, sig_type, action):
        return self.q_table.get(state_key, {}).get(sig_type, {}).get(action, 0.0)

    def _set_q(self, state_key, sig_type, action, value):
        if state_key not in self.q_table:
            self.q_table[state_key] = {}
        if sig_type not in self.q_table[state_key]:
            self.q_table[state_key][sig_type] = {}
        self.q_table[state_key][sig_type][action] = value

    def _best_action(self, state_key, sig_type):
        """Return the action with highest Q-value for this state+signal."""
        q_vals = {a: self._get_q(state_key, sig_type, a) for a in ACTIONS}
        return max(q_vals, key=q_vals.get)

    # ── Decision making ───────────────────────────────────────────────────────

    def choose_actions(self, state: MarketState, active_signals: list) -> dict:
        """
        For each active signal type, choose an action (0=suppress, 1=include, 2=highlight).
        Uses epsilon-greedy: explore randomly or exploit best known action.

        Returns: {signal_type: action}
        """
        state_key = str(state.to_bucket())
        decisions = {}

        for sig in active_signals:
            sig_type = sig["type"]
            if sig_type not in SIGNAL_TYPES:
                decisions[sig_type] = 1  # default: include unknown types
                continue

            if random.random() < self.epsilon:
                # Explore: try a random action
                action = random.choice(ACTIONS)
            else:
                # Exploit: use best known action
                action = self._best_action(state_key, sig_type)

            # Override: dividend_capture with record date ≤ 3 days ALWAYS highlighted
            if sig_type == "dividend_capture":
                action = 2

            decisions[sig_type] = action

        return decisions

    def emphasis_label(self, action: int) -> str:
        return {0: "suppress", 1: "normal", 2: "highlight"}[action]

    # ── Learning ──────────────────────────────────────────────────────────────

    def learn(self, state: MarketState, sig_type: str, action: int,
              reward: float, next_state: MarketState):
        """
        Update Q-value using Bellman equation:
        Q(s,a) ← Q(s,a) + α * [r + γ * max_a'(Q(s',a')) - Q(s,a)]
        """
        state_key      = str(state.to_bucket())
        next_state_key = str(next_state.to_bucket())

        current_q = self._get_q(state_key, sig_type, action)
        max_next_q = max(self._get_q(next_state_key, sig_type, a) for a in ACTIONS)

        new_q = current_q + LEARNING_RATE * (
            reward + DISCOUNT_FACTOR * max_next_q - current_q
        )
        self._set_q(state_key, sig_type, action, new_q)

    def end_episode(self):
        """Call after processing each day's outcomes."""
        self.episode_count += 1
        self.epsilon = max(EPSILON_MIN, self.epsilon * EPSILON_DECAY)
        self._save_to_db()
        print(f"  RL episode {self.episode_count} complete. "
              f"Epsilon (exploration rate): {self.epsilon:.3f}")

    # ── Introspection ─────────────────────────────────────────────────────────

    def print_policy(self):
        """Print what the agent has learned so far."""
        print("\nRL Agent learned policy:")
        print(f"  Exploration rate (epsilon): {self.epsilon:.3f}")
        print(f"  Episodes trained: {self.episode_count}")
        print()

        if not self.q_table:
            print("  No policy learned yet — need more outcome data (7+ days).")
            return

        for state_key, signals in list(self.q_table.items())[:5]:
            print(f"  State bucket {state_key}:")
            for sig_type, actions in signals.items():
                best = max(actions, key=actions.get)
                best_q = actions[best]
                label = self.emphasis_label(best)
                print(f"    {sig_type}: best action = {label} (Q={best_q:.3f})")
