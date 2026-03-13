"""
Simulated wallet for paper trading.

Provides a thread-safe fake wallet that tracks balance, positions, and
unrealised PnL without making any real exchange API calls.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def compute_unrealized_pnl(
    position: Dict[str, Any],
    current_price: float,
    margin_currency: str = "USDT",
    usdt_inr_rate: float = 1.0,
) -> Dict[str, float]:
    """Compute unrealized PnL for an open position.

    *current_price* and the position's *entry_price* are in USDT.
    When *margin_currency* is ``"INR"`` the PnL is converted so that
    ``pnl_local`` and ``pnl_percent`` are in the margin currency.

    Returns ``{"pnl_usdt": …, "pnl_local": …, "pnl_percent": …}``.
    """
    entry = position["entry_price"]
    qty = position["quantity"]
    margin = position["margin"]
    side = position["side"].lower()

    if side in ("long", "buy"):
        pnl_usdt = (current_price - entry) * qty
    else:
        pnl_usdt = (entry - current_price) * qty

    if margin_currency.upper() == "INR" and usdt_inr_rate > 0:
        pnl_local = pnl_usdt * usdt_inr_rate
    else:
        pnl_local = pnl_usdt

    pnl_pct = (pnl_local / margin) * 100 if margin > 0 else 0.0

    return {
        "pnl_usdt": round(pnl_usdt, 6),
        "pnl_local": round(pnl_local, 4),
        "pnl_percent": round(pnl_pct, 4),
    }


class SimWallet:
    """Thread-safe simulated futures wallet."""

    def __init__(self, initial_balance: float = 10_000.0, currency: str = "INR"):
        self._lock = threading.Lock()
        self.currency = currency
        self._initial_balance = initial_balance
        self.balance = initial_balance
        self.available_margin = initial_balance
        self.positions: List[Dict[str, Any]] = []

    def reset(
        self, balance: Optional[float] = None, currency: Optional[str] = None,
    ):
        with self._lock:
            if balance is not None:
                self._initial_balance = balance
            if currency is not None:
                self.currency = currency
            self.balance = self._initial_balance
            self.available_margin = self._initial_balance
            self.positions = []

    def get_balance(self) -> float:
        with self._lock:
            return self.available_margin

    def update_margin(self, amount: float) -> None:
        """Adjust available margin by *amount* (positive = credit)."""
        with self._lock:
            self.available_margin = max(self.available_margin + amount, 0.0)

    def open_position(
        self,
        pair: str,
        side: str,
        entry_price: float,
        quantity: float,
        margin: float,
        leverage: int,
    ) -> Dict[str, Any]:
        with self._lock:
            if margin > self.available_margin:
                raise ValueError(
                    f"Insufficient sim margin: need {margin:.2f}, "
                    f"have {self.available_margin:.2f}"
                )

            pos: Dict[str, Any] = {
                "id": f"sim_{int(time.time() * 1000)}",
                "pair": pair,
                "side": side.lower(),
                "entry_price": entry_price,
                "quantity": quantity,
                "margin": margin,
                "leverage": leverage,
                "timestamp": time.time(),
                "status": "open",
            }

            self.available_margin -= margin
            self.positions.append(pos)

            logger.info(
                "SIM: Opened %s %s — entry=%.4f qty=%.6f margin=%.2f",
                side, pair, entry_price, quantity, margin,
            )
            return pos.copy()

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        margin_currency: str = "USDT",
        usdt_inr_rate: float = 1.0,
    ) -> Dict[str, Any]:
        with self._lock:
            for pos in self.positions:
                if pos["id"] == position_id and pos["status"] == "open":
                    pnl = compute_unrealized_pnl(
                        pos, exit_price, margin_currency, usdt_inr_rate,
                    )

                    pos["status"] = "closed"
                    pos["exit_price"] = exit_price
                    pos["realized_pnl"] = pnl["pnl_local"]

                    self.available_margin += pos["margin"] + pnl["pnl_local"]
                    self.balance += pnl["pnl_local"]

                    logger.info(
                        "SIM: Closed %s — exit=%.4f PnL=%.2f (%.2f%%)",
                        position_id, exit_price,
                        pnl["pnl_local"], pnl["pnl_percent"],
                    )
                    return {"position": pos.copy(), **pnl}

            raise ValueError(f"No open sim position with id {position_id}")

    def get_open_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [p.copy() for p in self.positions if p["status"] == "open"]

    def get_position_by_pair(self, pair: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for p in self.positions:
                if p["pair"] == pair and p["status"] == "open":
                    return p.copy()
            return None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "balance": self.balance,
                "available_margin": self.available_margin,
                "currency": self.currency,
                "positions": [p.copy() for p in self.positions],
                "open_positions": [
                    p.copy() for p in self.positions if p["status"] == "open"
                ],
            }
