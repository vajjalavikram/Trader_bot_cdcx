"""
Monitor an open position and close it when TP or SL is reached.

The monitor runs in a tight loop (using the same check_frequency as the
entry scanner) and fetches the live market price each iteration.

Two exit mechanisms work in parallel:
  1. **Server-side TP/SL** — placed via ``create_tpsl`` at order time so
     CoinDCX's engine closes the position even if the bot goes offline.
  2. **Client-side check** — this module watches the price and calls
     ``exit_position`` if TP/SL is breached, acting as a safety net.
"""

import logging
import time
import threading
from typing import Any, Callable, Dict, Optional

from bot import config
from bot.exchange_precision import snap_price
from bot.execution import exit_position, get_position
from bot.market_data import fetch_current_price, fetch_usdt_inr_rate
from bot.sim_wallet import compute_unrealized_pnl

logger = logging.getLogger(__name__)


def compute_tp_sl(
    entry_price: float, direction: str, price_increment: float = 0.0,
):
    """Return (tp_price, sl_price) based on direction.

    LONG  → TP > entry_price, SL < entry_price
    SHORT → TP < entry_price, SL > entry_price

    When *price_increment* > 0 the prices are snapped to valid exchange ticks.
    """
    tp_pct = config.TAKE_PROFIT_PERCENT / 100
    sl_pct = config.STOP_LOSS_PERCENT / 100

    if direction == "LONG":
        tp_price = entry_price * (1 + tp_pct)
        sl_price = entry_price * (1 - sl_pct)
    else:
        tp_price = entry_price * (1 - tp_pct)
        sl_price = entry_price * (1 + sl_pct)

    if price_increment > 0:
        raw_tp, raw_sl = tp_price, sl_price
        tp_price = snap_price(tp_price, price_increment)
        sl_price = snap_price(sl_price, price_increment)
        if tp_price != raw_tp:
            logger.info("  TP price snapped: %.8f → %.8f", raw_tp, tp_price)
        if sl_price != raw_sl:
            logger.info("  SL price snapped: %.8f → %.8f", raw_sl, sl_price)
    else:
        tp_price = round(tp_price, 4)
        sl_price = round(sl_price, 4)

    logger.info(
        "Computed TP/SL for %s from entry %.4f: TP=%.4f  SL=%.4f  (TP%%=%.2f  SL%%=%.2f)",
        direction, entry_price, tp_price, sl_price,
        config.TAKE_PROFIT_PERCENT, config.STOP_LOSS_PERCENT,
    )

    return tp_price, sl_price


def monitor(
    entry_price: float,
    position_id: str,
    quantity: float = 0.0,
    margin: float = 0.0,
    margin_currency: str = "USDT",
    usdt_inr_rate: float = 1.0,
    stop_event: Optional[threading.Event] = None,
    status_callback: Optional[Callable] = None,
) -> str:
    """
    Block until the position hits TP or SL (or disappears from the server).

    Returns
    -------
    "tp" if take-profit was hit, "sl" if stop-loss, "closed" if the
    position was closed server-side, "stopped" if the stop_event fired.
    """

    def _status(**kw):
        if status_callback:
            status_callback(**kw)

    def _sleep(seconds: float) -> bool:
        if stop_event:
            stop_event.wait(timeout=seconds)
            return stop_event.is_set()
        time.sleep(seconds)
        return False

    direction = config.DIRECTION
    tp_price, sl_price = compute_tp_sl(entry_price, direction)
    side_str = "buy" if direction == "LONG" else "sell"
    _rate = usdt_inr_rate
    track_pnl = quantity > 0 and margin > 0

    logger.info(
        "Monitoring position %s — direction=%s entry=%.4f TP=%.4f SL=%.4f",
        position_id, direction, entry_price, tp_price, sl_price,
    )

    while True:
        if _sleep(config.CHECK_FREQUENCY_SECONDS):
            logger.info("Stop signal received during monitoring")
            return "stopped"

        try:
            current_price = fetch_current_price()
        except Exception as exc:
            logger.error("Price fetch failed during monitoring: %s", exc)
            continue

        if margin_currency == "INR":
            try:
                _rate = fetch_usdt_inr_rate()
            except Exception:
                pass

        pnl_data: Dict[str, float] = {}
        pos_info: Optional[Dict[str, Any]] = None
        if track_pnl:
            pnl_data = compute_unrealized_pnl(
                {"entry_price": entry_price, "quantity": quantity,
                 "margin": margin, "side": side_str},
                current_price, margin_currency, _rate,
            )
            pos_info = {
                "entry_price": entry_price,
                "current_price": current_price,
                "quantity": quantity,
                "margin": margin,
                "side": direction,
                "pnl_value": pnl_data.get("pnl_local", 0),
                "pnl_percent": pnl_data.get("pnl_percent", 0),
            }

        pnl_str = ""
        if pnl_data:
            pnl_str = (
                f" | PnL {pnl_data.get('pnl_local', 0):+.2f}"
                f" ({pnl_data.get('pnl_percent', 0):+.2f}%)"
            )

        logger.debug(
            "Monitor tick — price=%.4f (TP=%.4f, SL=%.4f)",
            current_price, tp_price, sl_price,
        )
        _status(
            current_price=current_price,
            unrealized_pnl=pnl_data.get("pnl_local"),
            pnl_percent=pnl_data.get("pnl_percent"),
            position_info=pos_info,
            position_status=(
                f"{direction} @ {entry_price:.4f} | "
                f"Now {current_price:.4f}{pnl_str} | "
                f"TP {tp_price:.4f} / SL {sl_price:.4f}"
            ),
        )

        hit: Optional[str] = None

        if direction == "LONG":
            if current_price >= tp_price:
                hit = "tp"
            elif current_price <= sl_price:
                hit = "sl"
        else:
            if current_price <= tp_price:
                hit = "tp"
            elif current_price >= sl_price:
                hit = "sl"

        if hit:
            logger.info("Exit condition reached: %s at price %.4f", hit.upper(), current_price)
            try:
                exit_position(position_id)
            except Exception as exc:
                logger.error("Exit call failed (server TP/SL may still close it): %s", exc)
            _status(unrealized_pnl=0.0, pnl_percent=0.0, position_info=None)
            return hit

        if _position_is_closed(position_id):
            logger.info("Position %s already closed server-side", position_id)
            _status(unrealized_pnl=0.0, pnl_percent=0.0, position_info=None)
            return "closed"


def _position_is_closed(position_id: str) -> bool:
    """Check whether the position's active_pos has returned to zero."""
    try:
        positions = get_position()
        if isinstance(positions, list):
            for pos in positions:
                if pos.get("id") == position_id:
                    return float(pos.get("active_pos", 1)) == 0.0
    except Exception as exc:
        logger.warning("Could not verify position status: %s", exc)
    return False
