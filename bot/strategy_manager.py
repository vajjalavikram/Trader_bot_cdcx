"""
Strategy manager for concurrent multi-strategy orchestration.

Each strategy gets its own ``StrategyConfig`` instance and runs in an
independent thread — no shared global config, no serialisation lock.

Portfolio-level risk guardrails (MAX_PORTFOLIO_MARGIN) prevent over-
allocation across strategies.
"""

import logging
import os
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

    _HEARTBEAT_POLL_INTERVAL = 10   # seconds between monitor sweeps
    _HEARTBEAT_STALE_THRESHOLD = 30  # seconds before a heartbeat is stale
    MAX_CONCURRENT_STRATEGIES = 20

    def __init__(self, max_portfolio_margin: float = 50_000.0):
        self._lock = threading.RLock()
        self._strategies: Dict[str, Dict[str, Any]] = {}
        self._counter = 0
        self._max_portfolio_margin = max_portfolio_margin
        self._recovering = False
        self.logs: deque = deque(maxlen=2000)

        self._active_threads: Dict[str, threading.Thread] = {}
        self._strategy_queue: deque = deque()

        self._hb_thread = threading.Thread(
            target=self._monitor_heartbeats, daemon=True,
        )
        self._hb_thread.start()

        self._queue_thread = threading.Thread(
            target=self._process_queue, daemon=True,
        )
        self._queue_thread.start()

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
                "last_heartbeat": time.time(),
            }
            logger.info(
                "Registered %s — %s %s %s notional=%.2f",
                sid, base, params.get("direction"), params.get("strategy_mode"), notional,
            )
        self._persist()
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

            running = sum(
                1 for t in self._active_threads.values() if t.is_alive()
            )
            if running >= self.MAX_CONCURRENT_STRATEGIES:
                strat["status"] = "queued"
                strat["phase"] = "Queued"
                if strategy_id not in self._strategy_queue:
                    self._strategy_queue.append(strategy_id)
                logger.info(
                    "Strategy %s queued due to concurrency limit (%d/%d)",
                    strategy_id, running, self.MAX_CONCURRENT_STRATEGIES,
                )
                self.logs.append(
                    f"Strategy {strategy_id} queued due to concurrency limit"
                )
                self._persist()
                return True

            self._launch_thread(strategy_id, strat)
        self._persist()
        return True

    def _launch_thread(self, strategy_id: str, strat: dict) -> None:
        """Start a strategy thread and register it (caller holds lock)."""
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
        self._active_threads[strategy_id] = t
        t.start()

    def stop_strategy(self, strategy_id: str) -> bool:
        with self._lock:
            strat = self._strategies.get(strategy_id)
            if not strat:
                return False
            strat["stop_event"].set()
            strat["phase"] = "Stopping"
        self._persist()
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
        self._persist()
        return True

    # ------------------------------------------------------------------
    # State updates (called by the bot's status_callback)
    # ------------------------------------------------------------------
    def update_strategy_state(self, strategy_id: str, updates: dict) -> None:
        with self._lock:
            strat = self._strategies.get(strategy_id)
            if not strat:
                return

            strat["last_heartbeat"] = time.time()

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

            # -- Trade logging on confirmed position events ----
            if phase == "Positioned" and pos_info:
                self._log_trade_event(
                    strategy_id, "ENTRY",
                    pos_info.get("entry_price", 0),
                    pos_info.get("quantity", 0),
                )
            elif phase in ("Done", "Expired") and strat.get("entry_price"):
                self._log_trade_event(
                    strategy_id, "EXIT",
                    strat.get("current_price") or strat.get("entry_price", 0),
                    strat.get("quantity", 0),
                )

            should_persist = any(
                k in updates for k in ("phase", "position_info", "position_status")
            )
        if should_persist:
            self._persist()

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
            with self._lock:
                self._active_threads.pop(strategy_id, None)
            self._persist()

    # ------------------------------------------------------------------
    # Heartbeat monitor
    # ------------------------------------------------------------------
    def _monitor_heartbeats(self) -> None:
        """Background loop that detects silently crashed strategy threads."""
        while True:
            time.sleep(self._HEARTBEAT_POLL_INTERVAL)
            if self._recovering:
                continue
            try:
                self._sweep_stale_heartbeats()
            except Exception as exc:
                logger.debug("Heartbeat sweep error: %s", exc)

    def _sweep_stale_heartbeats(self) -> None:
        now = time.time()
        stale_ids: List[str] = []

        with self._lock:
            for sid, strat in self._strategies.items():
                if strat["status"] not in _ACTIVE_STATUSES:
                    continue
                hb = strat.get("last_heartbeat")
                if hb is None:
                    continue
                thread = strat.get("thread")
                thread_dead = thread is None or not thread.is_alive()
                if thread_dead and (now - hb) > self._HEARTBEAT_STALE_THRESHOLD:
                    stale_ids.append(sid)

        for sid in stale_ids:
            logger.warning(
                "Heartbeat stale for strategy %s, restarting", sid,
            )
            self.logs.append(
                f"Strategy {sid} restarted due to stale heartbeat"
            )
            self._restart_stale_strategy(sid)

    def _restart_stale_strategy(self, strategy_id: str) -> None:
        """Reset a strategy whose thread died and restart it."""
        with self._lock:
            strat = self._strategies.get(strategy_id)
            if not strat:
                return
            self._active_threads.pop(strategy_id, None)
            strat["status"] = "waiting"
            strat["phase"] = "Recovering"
            strat["thread"] = None
            strat["stop_event"] = threading.Event()
            strat["last_heartbeat"] = time.time()
        self.start_strategy(strategy_id)

    # ------------------------------------------------------------------
    # Strategy queue processor
    # ------------------------------------------------------------------
    def _process_queue(self) -> None:
        """Background loop that starts queued strategies when capacity frees."""
        while True:
            time.sleep(5)
            if self._recovering:
                continue
            try:
                self._drain_queue()
            except Exception as exc:
                logger.debug("Queue processor error: %s", exc)

    def _drain_queue(self) -> None:
        """Pop queued strategies and launch them if below the limit."""
        launched = False
        while self._strategy_queue:
            with self._lock:
                running = sum(
                    1 for t in self._active_threads.values() if t.is_alive()
                )
                if running >= self.MAX_CONCURRENT_STRATEGIES:
                    break
                if not self._strategy_queue:
                    break
                sid = self._strategy_queue.popleft()
                strat = self._strategies.get(sid)
                if not strat or strat["status"] != "queued":
                    continue
                if strat["stop_event"].is_set():
                    strat["status"] = "stopped"
                    strat["phase"] = "Stopped"
                    continue

                self._launch_thread(sid, strat)
                launched = True

                logger.info("Starting queued strategy %s", sid)
                self.logs.append(f"Starting queued strategy {sid}")

        if launched:
            self._persist()

    # ------------------------------------------------------------------
    # Trade event logger
    # ------------------------------------------------------------------
    def _log_trade_event(
        self, strategy_id: str, side: str, price: float, quantity: float,
    ) -> None:
        """Best-effort write of an ENTRY/EXIT event to the trades table."""
        try:
            from db.trade_logger import log_trade
            log_trade(
                strategy_id=strategy_id,
                user_id="default_user",
                side=side,
                price=price or 0,
                quantity=quantity or 0,
                timestamp=int(time.time()),
            )
        except Exception as exc:
            logger.debug("Trade event logging failed: %s", exc)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _export_state(self) -> List[Dict[str, Any]]:
        """Return a JSON-serialisable snapshot of every strategy.

        Must be called while holding ``self._lock`` (or via RLock re-entry).
        Thread-unsafe fields (``thread``, ``stop_event``) are omitted;
        ``params`` has secrets stripped by the persistence layer.
        """
        with self._lock:
            result = []
            for strat in self._strategies.values():
                entry = {}
                for k, v in strat.items():
                    if k in ("thread", "stop_event"):
                        continue
                    entry[k] = v
                result.append(entry)
            return result

    def _persist(self) -> None:
        """Best-effort write of the current state to disk.

        Skipped while ``_recovering`` is ``True`` so that the half-rebuilt
        registry is never flushed to disk.
        """
        if self._recovering:
            return
        try:
            from bot.persistence import save_session_state
            data = self._export_state()
            save_session_state(data)
        except Exception as exc:
            logger.debug("Persist failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Session recovery
    # ------------------------------------------------------------------
    _RECOVERABLE_STATUSES = frozenset({
        "starting", "queued", "scanning", "order_placed", "position_open",
    })

    def recover_session(self) -> None:
        """Load persisted state and restart strategies that were active.

        Persistence is suppressed during the rebuild so that the half-
        populated registry never overwrites the saved file.  API credentials
        are read from environment variables (``COINDCX_API_KEY`` /
        ``COINDCX_API_SECRET``) since secrets are never written to disk.
        """
        from bot.persistence import load_session_state

        logger.info("Recovering previous session…")

        self._recovering = True
        try:
            self._recover_inner()
        finally:
            self._recovering = False

        self._persist()

    def _recover_inner(self) -> None:
        """Heavy-lifting for recovery — called with ``_recovering=True``."""
        from bot.persistence import load_session_state

        state = load_session_state()
        saved_strategies = state.get("strategies", [])
        if not saved_strategies:
            logger.info("No strategies to recover.")
            return

        api_key = os.getenv("COINDCX_API_KEY", "")
        api_secret = os.getenv("COINDCX_API_SECRET", "")

        recovered_total = 0
        restart_ids: List[str] = []
        now = time.time()

        with self._lock:
            for saved in saved_strategies:
                sid = saved.get("id")
                if not sid:
                    continue
                if sid in self._strategies:
                    continue

                num = int(sid.lstrip("S") or "0")
                if num > self._counter:
                    self._counter = num

                params = saved.get("params", {})
                params["api_key"] = api_key
                params["api_secret"] = api_secret

                pair_raw = saved.get("pair", params.get("pair", "B-BTC_USDT"))

                self._strategies[sid] = {
                    "id": sid,
                    "params": params,
                    "pair": pair_raw,
                    "pair_label": saved.get("pair_label", pair_raw.replace("B-", "").split("_")[0]),
                    "direction": saved.get("direction", params.get("direction", "LONG")),
                    "strategy_mode": saved.get("strategy_mode", params.get("strategy_mode", "momentum")),
                    "trading_mode": saved.get("trading_mode", params.get("trading_mode", "simulation")),
                    "status": saved.get("status", "waiting"),
                    "phase": saved.get("phase", "Recovered"),
                    "entry_price": saved.get("entry_price"),
                    "tp_price": saved.get("tp_price"),
                    "sl_price": saved.get("sl_price"),
                    "quantity": saved.get("quantity"),
                    "margin": saved.get("margin", 0),
                    "notional": saved.get("notional", 0),
                    "leverage": saved.get("leverage", 10),
                    "current_price": saved.get("current_price"),
                    "pnl": saved.get("pnl", 0.0),
                    "pnl_percent": saved.get("pnl_percent", 0.0),
                    "wallet_balance": saved.get("wallet_balance"),
                    "strategy_expiry_minutes": saved.get("strategy_expiry_minutes", 1440),
                    "dip_percent": saved.get("dip_percent", 5.0),
                    "error": saved.get("error"),
                    "error_detail": saved.get("error_detail"),
                    "thread": None,
                    "stop_event": threading.Event(),
                    "created_at": saved.get("created_at", time.time()),
                    "last_heartbeat": now,
                }
                recovered_total += 1

                if saved.get("status") in self._RECOVERABLE_STATUSES:
                    restart_ids.append(sid)

        recovered_active = 0
        for sid in restart_ids:
            if self.start_strategy(sid):
                recovered_active += 1
                logger.info("Restarted recovered strategy %s", sid)

        if recovered_total > 0:
            logger.info(
                "Recovered %d strategies from previous session (%d restarted)",
                recovered_total, recovered_active,
            )
            self.logs.append(
                f"Recovered {recovered_total} strategies from previous session "
                f"({recovered_active} restarted)"
            )
        else:
            logger.info("No strategies to recover.")
