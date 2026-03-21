"""Persist strategy state for session recovery after server restarts.

Active backend: **SQLite** (``data/trading_bot.db``).
The previous JSON backend is preserved below as ``_json_*`` functions
and can be re-enabled by swapping the top-level function bodies.

API secrets are never written to storage — on recovery, credentials are
read from environment variables.
"""

import json
import logging
import os
import threading
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── Shared constants ──────────────────────────────────────────────────────

_SECRET_KEYS = frozenset({"api_key", "api_secret"})
_SKIP_KEYS = frozenset({"thread", "stop_event", "is_alive"})

_DEFAULT_USER = "default_user"

# ── Lazy DB initialisation ────────────────────────────────────────────────

_db_ready = False
_db_lock = threading.Lock()


def _ensure_db() -> None:
    global _db_ready
    if _db_ready:
        return
    with _db_lock:
        if _db_ready:
            return
        from db.models import initialize_database
        initialize_database()
        _db_ready = True


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API  (called by StrategyManager — signatures unchanged)
# ═══════════════════════════════════════════════════════════════════════════

def load_session_state() -> Dict[str, Any]:
    """Load all persisted strategies from SQLite.

    Returns ``{"strategies": []}`` on any error.  Never raises.
    """
    _ensure_db()
    try:
        from db.database import get_connection
        conn = get_connection()
        rows = conn.execute("SELECT * FROM strategies").fetchall()
    except Exception as exc:
        logger.warning("SQLite load failed (starting fresh): %s", exc)
        return {"strategies": []}

    strategies: List[Dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        params = _safe_json_loads(r.get("params_json"))
        error_detail = _safe_json_loads(r.get("error_detail_json"))

        strategies.append({
            "id": r["strategy_id"],
            "params": params,
            "pair": r.get("pair"),
            "pair_label": r.get("pair_label"),
            "direction": r.get("direction"),
            "strategy_mode": r.get("strategy_mode"),
            "trading_mode": r.get("trading_mode"),
            "status": r.get("status"),
            "phase": r.get("phase"),
            "entry_price": r.get("entry_price"),
            "tp_price": r.get("tp_price"),
            "sl_price": r.get("sl_price"),
            "quantity": r.get("quantity"),
            "margin": r.get("margin", 0),
            "notional": r.get("notional", 0),
            "leverage": r.get("leverage", 10),
            "current_price": r.get("current_price"),
            "pnl": r.get("pnl", 0.0),
            "pnl_percent": r.get("pnl_percent", 0.0),
            "wallet_balance": r.get("wallet_balance"),
            "strategy_expiry_minutes": r.get("strategy_expiry_minutes", 1440),
            "dip_percent": r.get("dip_percent", 5.0),
            "error": r.get("error"),
            "error_detail": error_detail,
            "created_at": r.get("created_at"),
            "last_heartbeat": r.get("last_heartbeat"),
        })

    return {"strategies": strategies}


def save_session_state(strategies: List[Dict[str, Any]]) -> None:
    """Persist the full strategy list to SQLite.

    Performs an atomic upsert of every strategy and removes rows that
    are no longer present in the list (i.e. strategies that were deleted
    from the manager).
    """
    _ensure_db()

    sanitized = _sanitize(strategies)

    from db.database import get_connection
    conn = get_connection()

    current_ids = [s["id"] for s in sanitized if s.get("id")]

    with conn:
        if current_ids:
            placeholders = ",".join("?" * len(current_ids))
            conn.execute(
                f"DELETE FROM strategies WHERE strategy_id NOT IN ({placeholders})",
                current_ids,
            )
        else:
            conn.execute("DELETE FROM strategies")

        for s in sanitized:
            _upsert_strategy(conn, s)


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════

def _sanitize(strategies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip secrets and non-serialisable keys."""
    result: List[Dict[str, Any]] = []
    for strat in strategies:
        entry: Dict[str, Any] = {}
        for key, val in strat.items():
            if key in _SKIP_KEYS:
                continue
            if key == "params":
                entry[key] = {
                    pk: pv for pk, pv in val.items() if pk not in _SECRET_KEYS
                }
                continue
            entry[key] = val
        result.append(entry)
    return result


_UPSERT_SQL = """
    INSERT INTO strategies (
        strategy_id, user_id,
        pair, pair_label, direction, strategy_mode, trading_mode,
        notional, leverage, margin,
        dip_percent, strategy_expiry_minutes, tp_percent, sl_percent,
        status, phase,
        entry_price, tp_price, sl_price, quantity,
        current_price, pnl, pnl_percent, wallet_balance,
        error, error_detail_json, params_json,
        created_at, last_heartbeat
    ) VALUES (
        ?, ?,
        ?, ?, ?, ?, ?,
        ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?,
        ?, ?
    )
    ON CONFLICT (strategy_id) DO UPDATE SET
        user_id = EXCLUDED.user_id,
        pair = EXCLUDED.pair,
        pair_label = EXCLUDED.pair_label,
        direction = EXCLUDED.direction,
        strategy_mode = EXCLUDED.strategy_mode,
        trading_mode = EXCLUDED.trading_mode,
        notional = EXCLUDED.notional,
        leverage = EXCLUDED.leverage,
        margin = EXCLUDED.margin,
        dip_percent = EXCLUDED.dip_percent,
        strategy_expiry_minutes = EXCLUDED.strategy_expiry_minutes,
        tp_percent = EXCLUDED.tp_percent,
        sl_percent = EXCLUDED.sl_percent,
        status = EXCLUDED.status,
        phase = EXCLUDED.phase,
        entry_price = EXCLUDED.entry_price,
        tp_price = EXCLUDED.tp_price,
        sl_price = EXCLUDED.sl_price,
        quantity = EXCLUDED.quantity,
        current_price = EXCLUDED.current_price,
        pnl = EXCLUDED.pnl,
        pnl_percent = EXCLUDED.pnl_percent,
        wallet_balance = EXCLUDED.wallet_balance,
        error = EXCLUDED.error,
        error_detail_json = EXCLUDED.error_detail_json,
        params_json = EXCLUDED.params_json,
        created_at = EXCLUDED.created_at,
        last_heartbeat = EXCLUDED.last_heartbeat
"""


def _upsert_strategy(conn, s: Dict[str, Any]) -> None:
    sid = s.get("id")
    if not sid:
        return
    params = s.get("params", {})
    error_detail = s.get("error_detail")
    conn.execute(_UPSERT_SQL, (
        sid,
        _DEFAULT_USER,
        s.get("pair"),
        s.get("pair_label"),
        s.get("direction"),
        s.get("strategy_mode"),
        s.get("trading_mode"),
        s.get("notional"),
        s.get("leverage"),
        s.get("margin"),
        s.get("dip_percent"),
        s.get("strategy_expiry_minutes"),
        params.get("take_profit_percent"),
        params.get("stop_loss_percent"),
        s.get("status"),
        s.get("phase"),
        s.get("entry_price"),
        s.get("tp_price"),
        s.get("sl_price"),
        s.get("quantity"),
        s.get("current_price"),
        s.get("pnl", 0.0),
        s.get("pnl_percent", 0.0),
        s.get("wallet_balance"),
        s.get("error"),
        json.dumps(error_detail, default=str) if error_detail else None,
        json.dumps(params, default=str),
        s.get("created_at"),
        s.get("last_heartbeat"),
    ))


def _safe_json_loads(raw) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# LEGACY JSON BACKEND  (disabled — kept for rollback safety)
# ═══════════════════════════════════════════════════════════════════════════

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data",
)
_STATE_FILE = os.path.join(_DATA_DIR, "session_state.json")
_file_lock = threading.Lock()


def _json_load_session_state() -> Dict[str, Any]:  # noqa: F811
    """Load persisted state from JSON file (legacy)."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    if not os.path.exists(_STATE_FILE):
        return {"strategies": []}
    try:
        with _file_lock:
            with open(_STATE_FILE, "r") as fh:
                data = json.load(fh)
        if not isinstance(data, dict) or "strategies" not in data:
            raise ValueError("Unexpected session-state structure")
        return data
    except Exception as exc:
        logger.warning("JSON session-state load failed: %s", exc)
        return {"strategies": []}


def _json_save_session_state(strategies: List[Dict[str, Any]]) -> None:  # noqa: F811
    """Atomically write strategy list to JSON file (legacy)."""
    os.makedirs(_DATA_DIR, exist_ok=True)

    sanitized = _sanitize(strategies)
    state = {"strategies": sanitized}

    with _file_lock:
        tmp_path = _STATE_FILE + ".tmp"
        with open(tmp_path, "w") as fh:
            json.dump(state, fh, indent=2, default=str)
        os.replace(tmp_path, _STATE_FILE)
