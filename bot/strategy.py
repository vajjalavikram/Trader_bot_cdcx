"""
Evaluate Dip / Rise entry conditions.

Supports two modes:
  - **momentum**: enter in the direction of the move
    (LONG when price rose, SHORT when price fell)
  - **reversal**: enter against the move
    (LONG when price fell, SHORT when price rose)
"""

import logging
from typing import Optional

from bot.strategy_config import StrategyConfig, resolve_cfg

logger = logging.getLogger(__name__)


def evaluate(
    current_price: float,
    past_price: float,
    cfg: Optional[StrategyConfig] = None,
) -> Optional[str]:
    """
    Return the side to trade ("buy" or "sell") if the entry condition is met,
    otherwise return None.

    ``price_change`` is positive when price went up, negative when it went down.
    """
    cfg = resolve_cfg(cfg)

    if past_price == 0:
        logger.warning("Past price is zero — skipping evaluation")
        return None

    price_change = (current_price - past_price) / past_price * 100

    logger.info(
        "Price change over %d min: %.4f%% (current=%.4f, past=%.4f)",
        cfg.comparison_window_minutes,
        price_change,
        current_price,
        past_price,
    )

    direction = cfg.direction
    mode = cfg.strategy_mode
    threshold = cfg.dip_percent

    triggered = False

    if mode == "momentum":
        if direction == "LONG" and price_change >= threshold:
            triggered = True
        elif direction == "SHORT" and price_change <= -threshold:
            triggered = True
    elif mode == "reversal":
        if direction == "LONG" and price_change <= -threshold:
            triggered = True
        elif direction == "SHORT" and price_change >= threshold:
            triggered = True
    else:
        logger.error("Unknown strategy_mode: %s", mode)

    if triggered:
        side = "buy" if direction == "LONG" else "sell"
        logger.info("ENTRY TRIGGERED — mode=%s direction=%s side=%s", mode, direction, side)
        return side

    return None
