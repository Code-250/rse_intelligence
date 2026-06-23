"""
First-party storage — waitlist emails and a lightweight event log.

Everything here is OPTIONAL and degrades gracefully: if DATABASE_URL is not set
(or the DB is unreachable) the app still works, it just won't persist signups or
first-party analytics. GA4 remains the primary traffic measurement; this gives a
cookie-less, ad-blocker-proof backup count of real usage.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")


def _conn():
    """Open a psycopg2 connection, or return None if unavailable."""
    if not DATABASE_URL:
        return None
    try:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:  # noqa: BLE001
        logger.warning("[storage] DB connection failed: %s", e)
        return None


def init_storage() -> None:
    """Create the waitlist + events tables if a database is configured."""
    conn = _conn()
    if not conn:
        logger.info("[storage] No DATABASE_URL — waitlist/analytics persistence disabled.")
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fda_waitlist (
                    id         SERIAL PRIMARY KEY,
                    email      TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fda_events (
                    id         SERIAL PRIMARY KEY,
                    event      TEXT NOT NULL,
                    detail     TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
        logger.info("[storage] Tables ready.")
    except Exception as e:  # noqa: BLE001
        logger.warning("[storage] init failed: %s", e)
    finally:
        conn.close()


def record_event(event: str, detail: str = "") -> None:
    """Append a first-party analytics event (best-effort, never raises)."""
    conn = _conn()
    if not conn:
        return
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fda_events (event, detail) VALUES (%s, %s)",
                (event[:64], (detail or "")[:500]),
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("[storage] record_event skipped: %s", e)
    finally:
        conn.close()


def save_waitlist_email(email: str) -> bool:
    """Store a waitlist signup. Returns True if stored (or already present)."""
    conn = _conn()
    if not conn:
        return False
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fda_waitlist (email) VALUES (%s) ON CONFLICT (email) DO NOTHING",
                (email.strip().lower()[:255],),
            )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[storage] save_waitlist_email failed: %s", e)
        return False
    finally:
        conn.close()


def get_stats() -> dict:
    """Return first-party usage counts for the internal /api/stats view."""
    conn = _conn()
    if not conn:
        return {"enabled": False, "note": "Set DATABASE_URL to record first-party stats."}
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fda_waitlist")
            waitlist = cur.fetchone()[0]
            cur.execute("SELECT event, count(*) FROM fda_events GROUP BY event")
            events = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute(
                "SELECT count(*) FROM fda_events WHERE event = 'analyze_succeeded' "
                "AND created_at > now() - interval '24 hours'"
            )
            analyses_24h = cur.fetchone()[0]
        return {
            "enabled": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "waitlist_signups": waitlist,
            "analyses_last_24h": analyses_24h,
            "event_totals": events,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("[storage] get_stats failed: %s", e)
        return {"enabled": False, "error": str(e)}
    finally:
        conn.close()
