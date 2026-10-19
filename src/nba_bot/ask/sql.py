"""Runs Claude's SQL safely.

The real guard is the database login, nba_ask: it can read only the `ask` schema,
every transaction it opens is read-only, and every statement stops at 5 seconds
(migration 015). On top of that, this module accepts one SELECT (or WITH) at a time
and returns at most MAX_ROWS rows.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

import psycopg
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from nba_bot.config import settings

MAX_ROWS = 200
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_engine: Engine | None = None


class QueryError(ValueError):
    """A query refused or failed. The message goes back to Claude to fix."""


def engine() -> Engine:
    global _engine
    if _engine is None:
        if not settings.ask_db_password:
            raise QueryError("Ask is not set up: ASK_DB_PASSWORD is empty (see .env.example).")
        _engine = create_engine(settings.ask_database_url, pool_size=2, max_overflow=0,
                                pool_pre_ping=True)
    return _engine


def check(query: str) -> str:
    """The query, tidied, if it is a single SELECT; otherwise QueryError."""
    q = query.strip().rstrip(";").strip()
    bare = _COMMENTS.sub(" ", q).strip().lower()
    if ";" in bare:
        raise QueryError("one statement at a time")
    if not (bare.startswith("select") or bare.startswith("with")):
        raise QueryError("only SELECT (or WITH ... SELECT) queries are allowed")
    return q


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def run(query: str, max_rows: int = MAX_ROWS) -> dict:
    """{columns, rows, row_count, truncated} for one read-only query."""
    q = check(query)
    # The driver's own cursor, with no parameters: so a `%` in `ILIKE '%tatum%'` is
    # just a character, not a placeholder.
    raw = engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(q)
            columns = [d.name for d in cur.description or []]
            rows = cur.fetchmany(max_rows + 1)
        raw.rollback()
    except psycopg.Error as exc:
        raw.rollback()
        message = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        raise QueryError(message) from None
    finally:
        raw.close()
    return {"columns": columns, "rows": [[_plain(v) for v in r] for r in rows[:max_rows]],
            "row_count": min(len(rows), max_rows), "truncated": len(rows) > max_rows}
