"""API routes for strategy lifecycle, portfolio queries, and logs.

User identity is derived from the ``X-API-Key`` header (SHA-256 hash
of the CoinDCX API key).  User isolation is enforced at this layer —
each user can only see and control their own strategies.
``StrategyManager`` is unchanged.
"""

import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.security.identity import get_caller_id, get_user_id_from_api_key
from backend.security.crypto import encrypt_secret
from bot.strategy_manager import StrategyManager
from db.database import get_connection

logger = logging.getLogger(__name__)


# ── Request / response models ────────────────────────────────────────────

class StartStrategyRequest(BaseModel):
    params: Dict[str, Any]


class StopStrategyRequest(BaseModel):
    strategy_id: str


class MarginCheckRequest(BaseModel):
    margin: float


class MaxMarginRequest(BaseModel):
    value: float


class StartStrategyResponse(BaseModel):
    strategy_id: str
    started: bool


class SuccessResponse(BaseModel):
    success: bool


class MarginCheckResponse(BaseModel):
    allowed: bool
    message: str


class SettingsResponse(BaseModel):
    max_portfolio_margin: float


class SessionLoadRequest(BaseModel):
    api_key: str
    secret: str
    remember_secret: bool = False


class SessionLoadResponse(BaseModel):
    user_id: str
    strategies: List[Dict[str, Any]]


# ── Ownership registry (strategy_id → user_id) ──────────────────────────

_ownership: Dict[str, str] = {}
_ownership_lock = threading.Lock()


def _load_ownership() -> None:
    """Rebuild the ownership map from the strategies table."""
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT strategy_id, user_id FROM strategies",
        ).fetchall()
        with _ownership_lock:
            _ownership.clear()
            for row in rows:
                r = dict(row)
                _ownership[r["strategy_id"]] = r["user_id"]
    except Exception as exc:
        logger.debug("Could not load ownership map: %s", exc)


def _set_owner(strategy_id: str, user_id: str) -> None:
    with _ownership_lock:
        _ownership[strategy_id] = user_id
    try:
        conn = get_connection()
        with conn:
            conn.execute(
                "UPDATE strategies SET user_id = ? WHERE strategy_id = ?",
                (user_id, strategy_id),
            )
    except Exception as exc:
        logger.debug("Could not persist ownership for %s: %s", strategy_id, exc)


def _owns(strategy_id: str, user_id: str) -> bool:
    with _ownership_lock:
        return _ownership.get(strategy_id) == user_id


def _user_strategy_ids(user_id: str) -> frozenset:
    with _ownership_lock:
        return frozenset(
            sid for sid, uid in _ownership.items() if uid == user_id
        )


# ── User auto-creation helper ───────────────────────────────────────────

def _ensure_user(user_id: str) -> None:
    """Insert a user row if it doesn't already exist."""
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO users (user_id, created_at) VALUES (?, ?) "
            "ON CONFLICT DO NOTHING",
            (user_id, int(time.time())),
        )


# ── Router factory ───────────────────────────────────────────────────────

def create_router(manager: StrategyManager) -> APIRouter:
    """Build an ``APIRouter`` wired to the given manager instance."""

    _load_ownership()

    router = APIRouter()

    # ── Session ──────────────────────────────────────────────────────

    @router.post("/session/load", response_model=SessionLoadResponse)
    def load_session(req: SessionLoadRequest):
        """Identify user from API key, optionally store secret, return strategies."""
        logger.info(
            "POST /session/load — api_key=%s… remember=%s",
            req.api_key[:8] if req.api_key else "(empty)",
            req.remember_secret,
        )

        if not req.api_key:
            logger.warning("session/load called with empty api_key")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="api_key is required",
            )

        try:
            user_id = get_user_id_from_api_key(req.api_key)
            logger.info("Derived user_id=%s…", user_id[:12])
        except Exception as exc:
            logger.error("Failed to derive user_id: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Identity derivation failed: {exc}",
            )

        try:
            _ensure_user(user_id)
            logger.info("User row ensured for %s…", user_id[:12])
        except Exception as exc:
            logger.error("_ensure_user failed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database error creating user: {exc}",
            )

        if req.remember_secret:
            try:
                encrypted = encrypt_secret(req.secret)
            except RuntimeError as exc:
                logger.error("Encryption failed (ENCRYPTION_KEY missing?): %s", exc)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=str(exc),
                )

            try:
                conn = get_connection()
                existing = conn.execute(
                    "SELECT key_id FROM api_keys WHERE user_id = ? AND api_key = ?",
                    (user_id, req.api_key),
                ).fetchone()
                with conn:
                    if existing:
                        conn.execute(
                            "UPDATE api_keys SET encrypted_secret = ? WHERE key_id = ?",
                            (encrypted, dict(existing)["key_id"]),
                        )
                        logger.info("Updated encrypted secret for existing key")
                    else:
                        conn.execute(
                            "INSERT INTO api_keys "
                            "(key_id, user_id, exchange, api_key, encrypted_secret, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (uuid.uuid4().hex, user_id, "coindcx", req.api_key, encrypted, int(time.time())),
                        )
                        logger.info("Stored new encrypted API key")
            except HTTPException:
                raise
            except Exception as exc:
                logger.error("Failed to store API key: %s", exc, exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Database error storing key: {exc}",
                )

        owned = _user_strategy_ids(user_id)
        strategies = [
            s for s in manager.get_all_strategies()
            if s.get("id") in owned
        ]
        logger.info(
            "session/load success — user=%s… strategies=%d",
            user_id[:12], len(strategies),
        )
        return SessionLoadResponse(user_id=user_id, strategies=strategies)

    # ── Strategy lifecycle ───────────────────────────────────────────

    @router.post("/strategy/start", response_model=StartStrategyResponse)
    def start_strategy(
        req: StartStrategyRequest,
        user_id: str = Depends(get_caller_id),
    ):
        """Register a strategy from *params* and start it immediately."""
        params = req.params.copy()
        if not params.get("api_key"):
            params["api_key"] = os.getenv("COINDCX_API_KEY", "")
        if not params.get("api_secret"):
            params["api_secret"] = os.getenv("COINDCX_API_SECRET", "")
        sid = manager.register_strategy(params)
        _set_owner(sid, user_id)
        ok = manager.start_strategy(sid)
        return StartStrategyResponse(strategy_id=sid, started=ok)

    @router.post("/strategy/stop", response_model=SuccessResponse)
    def stop_strategy(
        req: StopStrategyRequest,
        user_id: str = Depends(get_caller_id),
    ):
        if not _owns(req.strategy_id, user_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your strategy")
        ok = manager.stop_strategy(req.strategy_id)
        return SuccessResponse(success=ok)

    @router.delete("/strategy/{strategy_id}", response_model=SuccessResponse)
    def remove_strategy(
        strategy_id: str,
        user_id: str = Depends(get_caller_id),
    ):
        if not _owns(strategy_id, user_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your strategy")
        ok = manager.remove_strategy(strategy_id)
        if ok:
            with _ownership_lock:
                _ownership.pop(strategy_id, None)
        return SuccessResponse(success=ok)

    @router.post("/strategy/margin-check", response_model=MarginCheckResponse)
    def margin_check(
        req: MarginCheckRequest,
        user_id: str = Depends(get_caller_id),
    ):
        allowed, msg = manager.can_start_strategy(req.margin)
        return MarginCheckResponse(allowed=allowed, message=msg)

    # ── Queries ──────────────────────────────────────────────────────

    @router.get("/strategies")
    def list_strategies(
        user_id: str = Depends(get_caller_id),
    ) -> List[Dict[str, Any]]:
        owned = _user_strategy_ids(user_id)
        return [
            s for s in manager.get_all_strategies()
            if s.get("id") in owned
        ]

    @router.get("/strategies/active/ids")
    def active_strategy_ids(
        user_id: str = Depends(get_caller_id),
    ) -> List[str]:
        owned = _user_strategy_ids(user_id)
        return [
            sid for sid in manager.get_active_strategy_ids()
            if sid in owned
        ]

    @router.get("/portfolio")
    def portfolio_summary(
        user_id: str = Depends(get_caller_id),
    ) -> Dict[str, Any]:
        owned = _user_strategy_ids(user_id)
        all_strats = manager.get_all_strategies()
        user_strats = [s for s in all_strats if s.get("id") in owned]

        active = 0
        total_pnl = 0.0
        winning = 0
        losing = 0
        margin_used = 0.0
        active_statuses = {"starting", "queued", "scanning", "order_placed", "position_open"}

        for s in user_strats:
            st = s.get("status", "")
            if st in active_statuses:
                active += 1
                margin_used += s.get("margin", 0) or 0
            pnl = s.get("pnl", 0) or 0
            total_pnl += pnl
            if st == "position_open":
                if pnl > 0:
                    winning += 1
                elif pnl < 0:
                    losing += 1

        return {
            "active_strategies": active,
            "total_strategies": len(user_strats),
            "total_pnl": total_pnl,
            "winning": winning,
            "losing": losing,
            "margin_used": margin_used,
            "margin_available": max(
                manager.max_portfolio_margin - margin_used, 0.0,
            ),
        }

    @router.get("/logs")
    def get_logs(
        limit: int = 200,
        user_id: str = Depends(get_caller_id),
    ) -> List[str]:
        owned = _user_strategy_ids(user_id)
        entries = list(manager.logs)
        filtered = [
            e for e in entries
            if any(f"[{sid}]" in e for sid in owned)
        ]
        return filtered[-limit:]

    # ── Settings ─────────────────────────────────────────────────────

    @router.get("/settings", response_model=SettingsResponse)
    def get_settings(user_id: str = Depends(get_caller_id)):
        return SettingsResponse(
            max_portfolio_margin=manager.max_portfolio_margin,
        )

    @router.put("/settings/max-margin", response_model=SettingsResponse)
    def set_max_margin(
        req: MaxMarginRequest,
        user_id: str = Depends(get_caller_id),
    ):
        manager.max_portfolio_margin = req.value
        return SettingsResponse(
            max_portfolio_margin=manager.max_portfolio_margin,
        )

    return router
