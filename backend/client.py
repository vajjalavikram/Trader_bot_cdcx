"""HTTP client that mirrors the ``StrategyManager`` interface.

When the environment variable ``BACKEND_URL`` is set, the Streamlit UI
instantiates a ``BackendClient`` instead of a local ``StrategyManager``.
Every public method matches the manager's signature so the rest of the UI
code is completely agnostic about which mode it is running in.

Identity is conveyed via the ``X-API-Key`` header on every request.
"""

import logging
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import requests as _req

logger = logging.getLogger(__name__)

_TIMEOUT = 3


class BackendClient:
    """Drop-in HTTP replacement for ``StrategyManager``."""

    def __init__(self, base_url: str, api_key: str = ""):
        self._url = base_url.rstrip("/")
        self._api_key = api_key
        self._logs: deque = deque(maxlen=2000)
        self._max_portfolio_margin: float = 50_000.0
        if self._api_key:
            self._sync_settings()

    def set_api_key(self, api_key: str) -> None:
        """Update the API key used for identity on all subsequent calls."""
        self._api_key = api_key

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        if self._api_key:
            return {"X-API-Key": self._api_key}
        return {}

    def _get(self, path: str, **kw):
        kw.setdefault("headers", {}).update(self._headers())
        return _req.get(f"{self._url}{path}", timeout=_TIMEOUT, **kw)

    def _post(self, path: str, **kw):
        kw.setdefault("headers", {}).update(self._headers())
        return _req.post(f"{self._url}{path}", timeout=_TIMEOUT, **kw)

    def _put(self, path: str, **kw):
        kw.setdefault("headers", {}).update(self._headers())
        return _req.put(f"{self._url}{path}", timeout=_TIMEOUT, **kw)

    def _delete(self, path: str, **kw):
        kw.setdefault("headers", {}).update(self._headers())
        return _req.delete(f"{self._url}{path}", timeout=_TIMEOUT, **kw)

    def _sync_settings(self) -> None:
        try:
            resp = self._get("/settings")
            if resp.ok:
                self._max_portfolio_margin = resp.json()["max_portfolio_margin"]
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------
    def load_session(
        self,
        api_key: str,
        secret: str,
        remember_secret: bool = False,
    ) -> Dict[str, Any]:
        """Call ``POST /session/load`` and return the response dict."""
        try:
            resp = _req.post(
                f"{self._url}/session/load",
                json={
                    "api_key": api_key,
                    "secret": secret,
                    "remember_secret": remember_secret,
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except _req.exceptions.RequestException as exc:
            logger.error("load_session failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Logs  (accessed as ``mgr.logs`` — must behave like a deque)
    # ------------------------------------------------------------------
    @property
    def logs(self) -> deque:
        try:
            resp = self._get("/logs")
            if resp.ok:
                self._logs = deque(resp.json(), maxlen=2000)
        except Exception:
            pass
        return self._logs

    # ------------------------------------------------------------------
    # Portfolio margin property
    # ------------------------------------------------------------------
    @property
    def max_portfolio_margin(self) -> float:
        self._sync_settings()
        return self._max_portfolio_margin

    @max_portfolio_margin.setter
    def max_portfolio_margin(self, value: float) -> None:
        self._max_portfolio_margin = value
        try:
            self._put("/settings/max-margin", json={"value": value})
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Strategy lifecycle
    # ------------------------------------------------------------------
    def register_strategy(self, params: dict) -> str:
        try:
            resp = self._post("/strategy/start", json={"params": params})
            resp.raise_for_status()
            return resp.json()["strategy_id"]
        except _req.exceptions.RequestException as exc:
            logger.error("register_strategy failed: %s", exc)
            raise RuntimeError("Backend unreachable — could not start strategy.") from exc

    def start_strategy(self, strategy_id: str) -> bool:
        """Backend's ``/strategy/start`` already starts the strategy."""
        return True

    def stop_strategy(self, strategy_id: str) -> bool:
        try:
            resp = self._post("/strategy/stop", json={"strategy_id": strategy_id})
            return resp.ok and resp.json().get("success", False)
        except _req.exceptions.RequestException as exc:
            logger.error("stop_strategy failed: %s", exc)
            return False

    def remove_strategy(self, strategy_id: str) -> bool:
        try:
            resp = self._delete(f"/strategy/{strategy_id}")
            return resp.ok and resp.json().get("success", False)
        except _req.exceptions.RequestException as exc:
            logger.error("remove_strategy failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get_all_strategies(self) -> List[Dict[str, Any]]:
        try:
            resp = self._get("/strategies")
            if resp.ok:
                return resp.json()
        except Exception:
            pass
        return []

    def get_active_strategy_ids(self) -> List[str]:
        try:
            resp = self._get("/strategies/active/ids")
            if resp.ok:
                return resp.json()
        except Exception:
            pass
        return []

    def get_portfolio_summary(self) -> Dict[str, Any]:
        fallback = {
            "active_strategies": 0, "total_strategies": 0,
            "total_pnl": 0.0, "winning": 0, "losing": 0,
            "margin_used": 0.0, "margin_available": 0.0,
        }
        try:
            resp = self._get("/portfolio")
            if resp.ok:
                return resp.json()
        except Exception:
            pass
        return fallback

    def can_start_strategy(self, required_margin: float) -> Tuple[bool, str]:
        try:
            resp = self._post(
                "/strategy/margin-check", json={"margin": required_margin},
            )
            if resp.ok:
                data = resp.json()
                return data["allowed"], data["message"]
        except Exception:
            pass
        return False, "Backend unreachable — cannot verify margin."

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def health_check(self) -> bool:
        """Return ``True`` if the backend responds to ``GET /health``."""
        try:
            resp = _req.get(f"{self._url}/health", timeout=2)
            return resp.ok
        except _req.exceptions.RequestException:
            return False

    # ------------------------------------------------------------------
    # No-ops (handled by the backend process)
    # ------------------------------------------------------------------
    def recover_session(self) -> None:
        """Recovery is handled by the backend at startup."""
