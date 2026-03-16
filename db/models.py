"""Database schema creation and migration.

``initialize_database()`` is idempotent — safe to call on every startup.
"""

import logging

from db.database import get_connection

logger = logging.getLogger(__name__)

_DEFAULT_USER = "default_user"


def _add_column(conn, table: str, column: str, col_type: str) -> None:
    """Best-effort ALTER TABLE ADD COLUMN (no-op if column already exists)."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except Exception:
        pass


def initialize_database() -> None:
    """Create all tables, indexes, and seed rows if they don't exist."""
    conn = get_connection()

    with conn:
        # ── Users ────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       TEXT PRIMARY KEY,
                email         TEXT,
                password_hash TEXT,
                created_at    INTEGER
            )
        """)

        # Migration for databases created before auth support
        _add_column(conn, "users", "email", "TEXT")
        _add_column(conn, "users", "password_hash", "TEXT")

        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email "
            "ON users(email) WHERE email IS NOT NULL"
        )

        # ── API keys (encrypted exchange credentials) ────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key_id           TEXT PRIMARY KEY,
                user_id          TEXT NOT NULL,
                exchange         TEXT NOT NULL,
                api_key          TEXT NOT NULL,
                encrypted_secret TEXT NOT NULL,
                created_at       INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_apikeys_user "
            "ON api_keys(user_id)"
        )

        # ── Strategies ───────────────────────────────────────────────
        # Includes every field StrategyManager persists so the round-
        # trip through load/save is lossless.  ``params_json`` and
        # ``error_detail_json`` hold nested dicts as JSON strings.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                strategy_id             TEXT PRIMARY KEY,
                user_id                 TEXT DEFAULT 'default_user',
                pair                    TEXT,
                pair_label              TEXT,
                direction               TEXT,
                strategy_mode           TEXT,
                trading_mode            TEXT,
                notional                REAL,
                leverage                INTEGER,
                margin                  REAL,
                dip_percent             REAL,
                strategy_expiry_minutes INTEGER,
                tp_percent              REAL,
                sl_percent              REAL,
                status                  TEXT,
                phase                   TEXT,
                entry_price             REAL,
                tp_price                REAL,
                sl_price                REAL,
                quantity                REAL,
                current_price           REAL,
                pnl                     REAL DEFAULT 0.0,
                pnl_percent             REAL DEFAULT 0.0,
                wallet_balance          REAL,
                error                   TEXT,
                error_detail_json       TEXT,
                params_json             TEXT,
                created_at              REAL,
                last_heartbeat          REAL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ── Positions ────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                position_id  TEXT PRIMARY KEY,
                strategy_id  TEXT,
                user_id      TEXT,
                entry_price  REAL,
                tp_price     REAL,
                sl_price     REAL,
                quantity     REAL,
                status       TEXT,
                opened_at    INTEGER,
                closed_at    INTEGER,
                FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ── Trades ───────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id     TEXT PRIMARY KEY,
                strategy_id  TEXT,
                user_id      TEXT,
                side         TEXT,
                price        REAL,
                quantity     REAL,
                timestamp    INTEGER,
                FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # ── Indexes ──────────────────────────────────────────────────
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_strategy_user "
            "ON strategies(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_position_strategy "
            "ON positions(strategy_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trade_strategy "
            "ON trades(strategy_id)"
        )

        # ── Seed default user ────────────────────────────────────────
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, created_at) "
            "VALUES (?, strftime('%s', 'now'))",
            (_DEFAULT_USER,),
        )
