"""
Activity logger — persists all agent interactions and events to PostgreSQL.

Tables (auto-created on startup):
  orch_sessions    — one row per conversation (web session or WhatsApp thread)
  orch_messages    — every message exchanged with an agent
  orch_activity    — structured log of what agents DID (ticket created, PR reviewed, etc.)
"""
import json
import logging
import os
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/rse_intelligence")


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Create orchestrator tables if they don't exist. Safe to call on every startup."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orch_sessions (
            id          SERIAL PRIMARY KEY,
            session_key TEXT UNIQUE NOT NULL,        -- "web:{uuid}" or "whatsapp:{phone}"
            agent_name  TEXT NOT NULL DEFAULT 'coordinator',
            channel     TEXT NOT NULL DEFAULT 'web', -- web | whatsapp
            started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_active TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS orch_messages (
            id          SERIAL PRIMARY KEY,
            session_key TEXT NOT NULL,
            role        TEXT NOT NULL,               -- user | assistant
            agent_name  TEXT NOT NULL,
            content     TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_orch_messages_session
            ON orch_messages (session_key, created_at DESC);

        CREATE TABLE IF NOT EXISTS orch_activity (
            id           SERIAL PRIMARY KEY,
            agent_name   TEXT NOT NULL,
            action_type  TEXT NOT NULL,              -- message | ticket_created | pr_reviewed | deploy | digest_sent
            summary      TEXT NOT NULL,
            metadata     JSONB,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_orch_activity_agent
            ON orch_activity (agent_name, created_at DESC);
    """)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("Orchestrator DB tables ready")


# ── Sessions ──────────────────────────────────────────────────────────────────

def get_or_create_session(session_key: str, channel: str = "web", agent_name: str = "coordinator") -> dict:
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        INSERT INTO orch_sessions (session_key, channel, agent_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (session_key) DO UPDATE
            SET last_active = NOW(), agent_name = EXCLUDED.agent_name
        RETURNING *
    """, (session_key, channel, agent_name))
    row = dict(cur.fetchone())
    conn.commit()
    cur.close()
    conn.close()
    return row


# ── Messages ──────────────────────────────────────────────────────────────────

def save_message(session_key: str, role: str, agent_name: str, content: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO orch_messages (session_key, role, agent_name, content)
        VALUES (%s, %s, %s, %s)
    """, (session_key, role, agent_name, content))
    conn.commit()
    cur.close()
    conn.close()


def get_conversation_history(session_key: str, limit: int = 20) -> list[dict]:
    """Return last N messages as [{role, content}] for LLM context."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT role, content FROM orch_messages
        WHERE session_key = %s
        ORDER BY created_at DESC
        LIMIT %s
    """, (session_key, limit))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    # Reverse so oldest is first (LLM expects chronological order)
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ── Activity log ──────────────────────────────────────────────────────────────

def log_activity(agent_name: str, action_type: str, summary: str, metadata: dict = None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO orch_activity (agent_name, action_type, summary, metadata)
        VALUES (%s, %s, %s, %s)
    """, (agent_name, action_type, summary, json.dumps(metadata) if metadata else None))
    conn.commit()
    cur.close()
    conn.close()


def get_recent_activity(hours: int = 24, limit: int = 50) -> list[dict]:
    """Pull recent activity across all agents for the dashboard."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT agent_name, action_type, summary, created_at
        FROM orch_activity
        WHERE created_at >= NOW() - INTERVAL '%s hours'
        ORDER BY created_at DESC
        LIMIT %s
    """, (hours, limit))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def get_daily_summary() -> dict:
    """Aggregate stats for the daily WhatsApp digest."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT
            agent_name,
            COUNT(*) as action_count,
            MAX(created_at) as last_active
        FROM orch_activity
        WHERE created_at >= NOW() - INTERVAL '24 hours'
        GROUP BY agent_name
        ORDER BY action_count DESC
    """)
    agent_stats = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT COUNT(*) as total_messages
        FROM orch_messages
        WHERE created_at >= NOW() - INTERVAL '24 hours'
          AND role = 'user'
    """)
    total_messages = cur.fetchone()["total_messages"]

    cur.close()
    conn.close()
    return {"agent_stats": agent_stats, "total_messages": total_messages}
