"""Database connection — SQLite locally, PostgreSQL on Railway.

``DATABASE_URL`` is read from the environment, falling back to a local
SQLite file for development.  ``get_connection()`` returns a connection
with a unified API:

- ``?`` placeholders for parameters
- ``row["column"]`` dict-like access on result rows
- ``with conn:`` for transactional commit/rollback
"""

import os
import sqlite3
import threading
from typing import Optional

from sqlalchemy import create_engine

# ── URL resolution ────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///trading_bot.db")

# Railway exposes postgres:// but SQLAlchemy requires postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_is_postgres = DATABASE_URL.startswith("postgresql")

# ── SQLAlchemy engine (used for URL parsing + available for future ORM) ───

_engine_kwargs: dict = {"pool_pre_ping": True}
if not _is_postgres:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **_engine_kwargs)

# ── Connection singletons ─────────────────────────────────────────────────

_sqlite_conn: Optional[sqlite3.Connection] = None
_sqlite_lock = threading.Lock()
_pg_local = threading.local()


def get_connection():
    """Return a shared connection compatible with the rest of the codebase.

    SQLite  → singleton ``sqlite3.Connection`` (thread-safe via WAL).
    Postgres → thread-local ``_PgConnection`` wrapper.
    """
    if _is_postgres:
        conn = getattr(_pg_local, "conn", None)
        if conn is None:
            _pg_local.conn = _create_pg_conn()
            conn = _pg_local.conn
        return conn

    global _sqlite_conn
    if _sqlite_conn is not None:
        return _sqlite_conn
    with _sqlite_lock:
        if _sqlite_conn is not None:
            return _sqlite_conn
        _sqlite_conn = _create_sqlite_conn()
    return _sqlite_conn


def is_postgres() -> bool:
    """True when the active database is PostgreSQL."""
    return _is_postgres


# ── SQLite ────────────────────────────────────────────────────────────────

def _create_sqlite_conn() -> sqlite3.Connection:
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.isabs(db_path):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, db_path)
    else:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ── PostgreSQL wrapper ────────────────────────────────────────────────────

class _PgConnection:
    """Wraps ``psycopg2`` to match the ``sqlite3.Connection`` API.

    - Converts ``?`` placeholders to ``%s``.
    - Uses ``RealDictCursor`` so rows support ``row["column"]``
      and ``dict(row)``.
    - ``with conn:`` begins a transaction (auto-commit otherwise).
    """

    def __init__(self, dsn: str):
        import psycopg2
        import psycopg2.extras
        self._extras = psycopg2.extras
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = True

    def execute(self, sql, params=None):
        pg_sql = sql.replace("?", "%s")
        cur = self._conn.cursor(cursor_factory=self._extras.RealDictCursor)
        cur.execute(pg_sql, params)
        return cur

    def __enter__(self):
        self._conn.autocommit = False
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.autocommit = True

    def close(self):
        self._conn.close()


def _create_pg_conn() -> _PgConnection:
    return _PgConnection(DATABASE_URL)
