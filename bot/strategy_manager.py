"""
Strategy manager for concurrent multi-strategy orchestration.

Each strategy gets its own ``StrategyConfig`` instance and runs in an
independent thread — no shared global config, no serialisation lock.

Portfolio-level risk guardrails (MAX_PORTFOLIO_MARGIN) prevent over-
allocation across strategies.
"""

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from bot.strategy_config import StrategyConfig

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = frozenset({
    "starting", "queued", "scanning", "order_placed", "position_open",
})


class _StrategyLogHandler(logging.Handler):
    """Routes log records into the manager's shared log deque, tagged with
    the strategy ID so the UI can attribute messages."""

    def __init__(self, logs: deque, strategy_id: str):
        super().__init__()
        self._logs = logs
        self.setFormatter(logging.Formatter(
            f"%(asctime)s [%(levelname)s] [{strategy_id}] %(message)s",
            datefmt="%H:%M:%S",
        ))

    def emit(self, record):
        try:
            self._logs.append(self.format(record))
        except Exception:
            pass


class StrategyManager:
    """Thread-safe registry and executor for multiple trading strategies."""

    def __init__(self, max_portfolio_margin: float = 50_000.0):
        self._lock = threading.Lock()
        self._strategies: Dict[str, Dict[str, Any]] = {}
        self._counter = 0
        self._max_portfolio_margin = max_portfolio_margin
        self.logs: deque = deque(maxlen=2000)

    # ------------------------------------------------------------------
    # Portfolio margin property
    # ------------------------------------------------------------------
    @property
    def max_portfolio_margin(self) -> float:
        return self._max_portfolio_margin

    @max_portfolio_margin.setter
    def max_portfolio_margin(self, value: float):
        self._max_portfolio_margin = max(value, 0.0)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register_strategy(self, params: dict) -> str:
        """Create a new strategy entry and return its ID."""
        with self._lock:
            self._counter += 1
            sid = f"S{self._counter:03d}"

            pair_raw = params.get("pair", "B-BTC_USDT")
            base = pair_raw.replace("B-", "").split("_")[0]
            notional = float(params.get("notional", 0))
            leverage = max(int(params.get("leverage", 10)), 1)
            margin = notional / leverage

            self._strategies[sid] = {
                "id": sid,
                "params": params.copy(),
                "pair": pair_raw,
                "pair_label": base,
                "direction": params.get("direction", "LONG"),
                "strategy_mode": params.get("strategy_mode", "momentum"),
                "trading_mode": params.get("trading_mode", "simulation"),
                "status": "waiting",
                "phase": "Registered",
                "entry_price": None,
                "tp_price": None,
                "sl_price": None,
                "quantity": None,
                "margin": margin,
                "notional": notional,
                "leverage": leverage,
                "current_price": None,
                "pnl": 0.0,
                "pnl_percent": 0.0,
                "wallet_balance": None,
                "strategy_expiry_minutes": int(params.get("strategy_expiry_minutes", 1440)),
                "dip_percent": float(params.get("dip_percent", 5.0)),
                "error": None,
                "error_detail": None,
                "thread": None,
                "stop_event": threading.Event(),
                "created_at": time.time(),
            }
            logger.info(
                "Registered %s — %s %s %s notional=%.2f",
                sid, base, params.get("direction"), params.get("strategy_mode"), notional,
            )
            return sid

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start_strategy(self, strategy_id: str) -> bool:
        with self._lock:
            strat = self._strategies.get(strategy_id)
            if not strat:
                return False
            if strat["status"] in _ACTIVE_STATUSES:
                return False

            strat["stop_event"].clear()
            strat["status"] = "starting"
            strat["phase"] = "Starting"
            strat["error"] = None
            strat["error_detail"] = None

            t = threading.Thread(
                target=self._run_strategy,
                args=(strategy_id,),
                daemon=True,
            )
            strat["thread"] = t
            t.start()
            return True

    def stop_strategy(self, strategy_id: str) -> bool:
        with self._lock:
            strat = self._strategies.get(strategy_id)
            if not strat:
                return False
            strat["stop_event"].set()
            strat["phase"] = "Stopping"
            return True

    def remove_strategy(self, strategy_id: str) -> bool:
        """Remove a finished strategy from the registry."""
        with self._lock:
            strat = self._strategies.get(strategy_id)
            if not strat:
                return False
            if strat["status"] in _ACTIVE_STATUSES:
                return False
            del self._strategies[strategy_id]
            return True

    # ------------------------------------------------------------------
    # State updates (called by the bot's status_callback)
    # ------------------------------------------------------------------
    def update_strategy_state(self, strategy_id: str, updates: dict) -> None:
        with self._lock:
            strat = self._strategies.get(strategy_id)
            if not strat:
                return

            phase = updates.get("phase")
            if phase:
                strat["phase"] = phase
                _PHASE_MAP = {
                    "Starting": "starting",
                    "Scanning": "scanning",
                    "Placing order": "order_placed",
                    "Positioned": "position_open",
                    "Stopping": "stopping",
                    "Stopped": "stopped",
                    "Expired": "expired",
                    "Done": "closed",
                    "Error": "error",
                }
                strat["status"] = _PHASE_MAP.get(phase, strat["status"])

            if updates.get("current_price") is not None:
                strat["current_price"] = updates["current_price"]
            if "wallet_balance" in updates:
                strat["wallet_balance"] = updates["wallet_balance"]
            if "error" in updates:
                strat["error"] = updates["error"]
            if "error_detail" in updates:
                strat["error_detail"] = updates["error_detail"]
            if "unrealized_pnl" in updates:
                strat["pnl"] = updates["unrealized_pnl"] or 0.0
            if "pnl_percent" in updates:
                strat["pnl_percent"] = updates["pnl_percent"] or 0.0

            pos_info = updates.get("position_info")
            if pos_info:
                strat["entry_price"] = pos_info.get("entry_price")
                strat["quantity"] = pos_info.get("quantity")
                if pos_info.get("margin"):
                    strat["margin"] = pos_info["margin"]

            pos_status = updates.get("position_status", "")
            if "TP=" in str(pos_status) and "SL=" in str(pos_status):
                try:
                    tp_part = pos_status.split("TP=")[1].split()[0]
                    sl_part = pos_status.split("SL=")[1].split()[0]
                    strat["tp_price"] = float(tp_part)
                    strat["sl_price"] = float(sl_part)
                except (IndexError, ValueError):
                    pass

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get_all_strategies(self) -> List[Dict[str, Any]]:
        """Return a snapshot of every strategy (safe for UI consumption)."""
        with self._lock:
            result = []
            for strat in self._strategies.values():
                entry = {
                    k: v for k, v in strat.items()
                    if k not in ("thread", "stop_event", "params")
                }
                entry["is_alive"] = (
                    strat["thread"] is not None and strat["thread"].is_alive()
                )
                result.append(entry)
            return result

    def get_strategy(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            strat = self._strategies.get(strategy_id)
            if not strat:
                return None
            entry = {
                k: v for k, v in strat.items()
                if k not in ("thread", "stop_event", "params")
            }
            entry["is_alive"] = (
                strat["thread"] is not None and strat["thread"].is_alive()
            )
            return entry

    def get_active_strategy_ids(self) -> List[str]:
        with self._lock:
            return [
                sid for sid, s in self._strategies.items()
                if s["status"] in _ACTIVE_STATUSES
                or (s["thread"] is not None and s["thread"].is_alive())
            ]

    # ------------------------------------------------------------------
    # Portfolio margin
    # ------------------------------------------------------------------
    def get_total_margin_used(self) -> float:
        """Sum margin of strategies that are consuming capital."""
        with self._lock:
            return sum(
                s.get("margin", 0) or 0
                for s in self._strategies.values()
                if s["status"] in _ACTIVE_STATUSES
            )

    def get_available_margin(self) -> float:
        return max(self._max_portfolio_margin - self.get_total_margin_used(), 0.0)

    def can_start_strategy(self, required_margin: float) -> Tuple[bool, str]:
        used = self.get_total_margin_used()
        available = self._max_portfolio_margin - used
        if required_margin > available:
            return False, (
                f"Portfolio margin limit exceeded. "
                f"Required: ₹{required_margin:,.2f} | "
                f"Available: ₹{available:,.2f} | "
                f"Limit: ₹{self._max_portfolio_margin:,.2f}"
            )
        return True, ""

    # ------------------------------------------------------------------
    # Summary metrics
    # ------------------------------------------------------------------
    def get_portfolio_summary(self) -> Dict[str, Any]:
        with self._lock:
            active = 0
            total_pnl = 0.0
            winning = 0
            losing = 0
            margin_used = 0.0
            for s in self._strategies.values():
                if s["status"] in _ACTIVE_STATUSES:
                    active += 1
                    margin_used += s.get("margin", 0) or 0
                pnl = s.get("pnl", 0) or 0
                total_pnl += pnl
                if s["status"] == "position_open":
                    if pnl > 0:
                        winning += 1
                    elif pnl < 0:
                        losing += 1
            return {
                "active_strategies": active,
                "total_strategies": len(self._strategies),
                "total_pnl": total_pnl,
                "winning": winning,
                "losing": losing,
                "margin_used": margin_used,
                "margin_available": max(self._max_portfolio_margin - margin_used, 0.0),
            }

    # ------------------------------------------------------------------
    # Internal: strategy thread target (no config lock needed)
    # ------------------------------------------------------------------
    def _run_strategy(self, strategy_id: str):
        """Each strategy builds its own StrategyConfig and runs independently."""
        strat = self._strategies.get(strategy_id)
        if not strat:
            return

        params = strat["params"]
        stop_event = strat["stop_event"]

        handler = _StrategyLogHandler(self.logs, strategy_id)
        bot_logger = logging.getLogger("bot")
        bot_logger.addHandler(handler)
        bot_logger.setLevel(logging.DEBUG)

        try:
            cfg = StrategyConfig.from_params(params)

            sim_wallet = None
            if cfg.trading_mode == "simulation":
                from bot.sim_wallet import SimWallet
                sim_wallet = SimWallet(
                    initial_balance=cfg.sim_balance,
                    currency=cfg.margin_currency,
                )

            from bot.main import run
            run(
                strategy_config=cfg,
                stop_event=stop_event,
                status_callback=lambda **kw: self.update_strategy_state(
                    strategy_id, kw,
                ),
                sim_wallet=sim_wallet,
            )

            if stop_event.is_set():
                self.update_strategy_state(strategy_id, {"phase": "Stopped"})
            else:
                final_phase = strat.get("phase", "")
                if final_phase not in ("Done", "Expired", "Error"):
                    self.update_strategy_state(strategy_id, {"phase": "Done"})

        except Exception as exc:
            self.update_strategy_state(
                strategy_id, {"phase": "Error", "error": str(exc)},
            )
            logger.error("Strategy %s crashed: %s", strategy_id, exc, exc_info=True)
        finally:
            bot_logger.removeHandler(handler)
