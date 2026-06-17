"""
Database connection helpers.

A thin layer over psycopg2 — the rest of the codebase uses these helpers rather
than opening connections directly, so connection handling stays consistent.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import RealDictCursor

from config import get_settings

logger = logging.getLogger(__name__)


def get_connection() -> PgConnection:
    """Open a new PostgreSQL connection using `DATABASE_URL`.

    Raises:
        psycopg2.OperationalError: if the database is unreachable.
    """
    settings = get_settings()
    return psycopg2.connect(settings.database_url)


@contextmanager
def db_cursor(commit: bool = False) -> Iterator[RealDictCursor]:
    """Yield a dict-returning cursor, committing on success when `commit=True`.

    The connection and cursor are always closed. On error the transaction is
    rolled back and the exception re-raised (no silent failures).
    """
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        logger.error("Database operation failed; transaction rolled back", exc_info=True)
        raise
    finally:
        cur.close()
        conn.close()
