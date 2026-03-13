"""
Exchange precision utilities for CoinDCX.

All prices and quantities sent to the exchange must comply with the
instrument's increment rules.  These helpers snap values to valid
ticks, preventing HTTP 422 precision errors.
"""

import math


def snap_price(price: float, increment: float) -> float:
    """Round *price* down to the nearest valid price increment."""
    if increment <= 0:
        return price
    return round(math.floor(price / increment) * increment, 8)


def snap_quantity(qty: float, step: float) -> float:
    """Round *qty* down to the nearest valid quantity step."""
    if step <= 0:
        return qty
    return round(math.floor(qty / step) * step, 8)
