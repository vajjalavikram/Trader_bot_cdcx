"""SQLite connection helper.

Provides a lazily-created, thread-safe singleton connection with WAL
journal mode for concurrent reads/writes from strategy threads.
"""

import os
import sqlite3
import threading
from typing import Optional

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data",
)
DB_PATH = os.path.join(_DATA_DIR, "trading_bot.db")

_conn: Optional[sqlite3.Connection] = None
_init_lock = threading.Lock()


def get_connection() -> sqlite3.Connection:
    """Return the shared SQLite connection (created on first call)."""
    global _conn
    if _conn is not None:
        return _conn
    with _init_lock:
        if _conn is not None:
            return _conn
        os.makedirs(_DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        _conn = conn
    return _conn
