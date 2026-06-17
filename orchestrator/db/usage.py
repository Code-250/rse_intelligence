"""
Usage tracking — records every Claude API call with token counts and cost.

Table: orch_usage
  agent_name    — which agent made the call
  model         — claude-haiku / claude-sonnet / etc.
  input_tokens  — tokens in the prompt
  output_tokens — tokens in the response
  cost_usd      — calculated cost for this call
  created_at    — timestamp

Queries:
  save_usage()          — persist one call
  get_monthly_spend()   — total USD this calendar month
  get_usage_breakdown() — per-agent totals for dashboard
  get_daily_usage()     — last 30 days for chart
"""
import logging
from datetime import datetime, timezone

from orchestrator.db.activity import db_available, get_conn

logger = logging.getLogger(__name__)


def init_usage_table() -> None:
    """Create orch_usage table if it doesn't exist. Called on startup."""
    if not db_available():
        return
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orch_usage (
                id            SERIAL PRIMARY KEY,
                agent_name    TEXT NOT NULL,
                model         TEXT NOT NULL,
                input_tokens  INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd      NUMERIC(10,6) NOT NULL DEFAULT 0,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_orch_usage_agent
                ON orch_usage (agent_name, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_orch_usage_date
                ON orch_usage (created_at DESC);
        """)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("orch_usage table ready")
    except Exception as e:
        logger.warning("Could not create orch_usage table: %s", e)


def save_usage(usage: dict) -> None:
    """Persist one API call's usage. Silent no-op if DB unavailable."""
    if not db_available():
        return
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO orch_usage (agent_name, model, input_tokens, output_tokens, cost_usd)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            usage.get("agent_name", "unknown"),
            usage.get("model", "unknown"),
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("cost_usd", 0.0),
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning("Could not save usage: %s", e)


def get_monthly_spend() -> float:
    """Return total USD spent this calendar month."""
    if not db_available():
        return 0.0
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(cost_usd), 0)
            FROM orch_usage
            WHERE date_trunc('month', created_at) = date_trunc('month', NOW())
        """)
        result = cur.fetchone()[0]
        cur.close()
        conn.close()
        return float(result)
    except Exception as e:
        logger.warning("Could not get monthly spend: %s", e)
        return 0.0


def get_today_spend() -> float:
    """Return total USD spent so far today (server-local calendar day)."""
    if not db_available():
        return 0.0
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(cost_usd), 0)
            FROM orch_usage
            WHERE date_trunc('day', created_at) = date_trunc('day', NOW())
        """)
        result = cur.fetchone()[0]
        cur.close()
        conn.close()
        return float(result)
    except Exception as e:
        logger.warning("Could not get today's spend: %s", e)
        return 0.0


def get_usage_breakdown(days: int = 30) -> dict:
    """
    Return full usage breakdown for the dashboard:
      - per_agent: [{agent_name, calls, input_tokens, output_tokens, cost_usd}]
      - per_model: [{model, calls, cost_usd}]
      - daily: [{date, cost_usd, calls}] — last N days
      - totals: {calls, input_tokens, output_tokens, cost_usd}
      - monthly_budget_usd: from env
      - monthly_spend_usd: this month's total
    """
    import os
    monthly_budget = float(os.getenv("MONTHLY_BUDGET_USD", "20.0"))

    if not db_available():
        return {
            "per_agent": [], "per_model": [], "daily": [],
            "totals": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
            "monthly_budget_usd": monthly_budget,
            "monthly_spend_usd": 0.0,
            "note": "Database not connected",
        }

    try:
        from psycopg2.extras import RealDictCursor
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Per agent
        cur.execute("""
            SELECT agent_name,
                   COUNT(*) as calls,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cost_usd) as cost_usd
            FROM orch_usage
            WHERE created_at >= NOW() - INTERVAL '%s days'
            GROUP BY agent_name
            ORDER BY cost_usd DESC
        """, (days,))
        per_agent = [dict(r) for r in cur.fetchall()]

        # Per model
        cur.execute("""
            SELECT model,
                   COUNT(*) as calls,
                   SUM(cost_usd) as cost_usd
            FROM orch_usage
            WHERE created_at >= NOW() - INTERVAL '%s days'
            GROUP BY model
            ORDER BY cost_usd DESC
        """, (days,))
        per_model = [dict(r) for r in cur.fetchall()]

        # Daily
        cur.execute("""
            SELECT DATE(created_at) as date,
                   COUNT(*) as calls,
                   SUM(cost_usd) as cost_usd
            FROM orch_usage
            WHERE created_at >= NOW() - INTERVAL '%s days'
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        """, (days,))
        daily = [{"date": str(r["date"]), "calls": r["calls"], "cost_usd": float(r["cost_usd"])}
                 for r in cur.fetchall()]

        # Totals
        cur.execute("""
            SELECT COUNT(*) as calls,
                   COALESCE(SUM(input_tokens), 0) as input_tokens,
                   COALESCE(SUM(output_tokens), 0) as output_tokens,
                   COALESCE(SUM(cost_usd), 0) as cost_usd
            FROM orch_usage
            WHERE created_at >= NOW() - INTERVAL '%s days'
        """, (days,))
        totals = dict(cur.fetchone())

        monthly_spend = get_monthly_spend()

        cur.close()
        conn.close()

        return {
            "per_agent":          per_agent,
            "per_model":          per_model,
            "daily":              daily,
            "totals":             {k: float(v) if k == "cost_usd" else int(v) for k, v in totals.items()},
            "monthly_budget_usd": monthly_budget,
            "monthly_spend_usd":  monthly_spend,
            "budget_pct":         round(monthly_spend / monthly_budget * 100, 1) if monthly_budget else 0,
        }

    except Exception as e:
        logger.error("Could not get usage breakdown: %s", e)
        return {"per_agent": [], "per_model": [], "daily": [], "totals": {}, "monthly_budget_usd": monthly_budget, "monthly_spend_usd": 0.0}
