"""Atomic trade-event logger backed by SQLite.

Every confirmed position open (ENTRY) and close (EXIT) is persisted
to the ``trades`` table so there is a complete audit trail.
"""

import logging
import uuid

from db.database import get_connection

logger = logging.getLogger(__name__)


def log_trade(
    strategy_id: str,
    user_id: str,
    side: str,
    price: float,
    quantity: float,
    timestamp: int,
) -> None:
    """Insert a single trade record atomically.

    Parameters
    ----------
    strategy_id : str
        Owning strategy (e.g. ``"S001"``).
    user_id : str
        User that owns the strategy (default: ``"default_user"``).
    side : str
        ``"ENTRY"`` or ``"EXIT"``.
    price : float
        Execution price.
    quantity : float
        Position quantity.
    timestamp : int
        Epoch seconds of the trade event.
    """
    trade_id = uuid.uuid4().hex
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "INSERT INTO trades "
                "(trade_id, strategy_id, user_id, side, price, quantity, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (trade_id, strategy_id, user_id, side, price, quantity, timestamp),
            )
        logger.info(
            "Trade logged for strategy %s: %s %.6f @ %.4f",
            strategy_id, side, quantity, price,
        )
    except Exception as exc:
        logger.debug("Trade logging failed (non-fatal): %s", exc)
