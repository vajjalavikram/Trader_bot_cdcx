"""
Fetch market prices from CoinDCX public endpoints.

Uses the candlestick API (1-minute resolution) to build a lightweight
in-memory price cache so we can compare current price to the price
X minutes ago without redundant API calls.

All functions accept an optional ``cfg`` (StrategyConfig) for per-strategy
isolation.  When omitted, global ``bot.config`` values are used as fallback.
"""

import logging
import time
from collections import deque
from typing import Any, Dict, Optional

import requests

from bot.strategy_config import StrategyConfig, resolve_cfg

logger = logging.getLogger(__name__)

# Each entry: (epoch_seconds, price)
_price_cache: deque[tuple[float, float]] = deque(maxlen=10_000)

# Caches
_usdt_inr_cache: Dict[str, Any] = {"rate": None, "ts": 0.0}
_instrument_cache: Dict[str, dict] = {}


def _request_with_retry(
    method: str,
    url: str,
    cfg: Optional[StrategyConfig] = None,
    **kwargs,
) -> requests.Response:
    cfg = resolve_cfg(cfg)
    for attempt in range(1, cfg.api_max_retries + 1):
        try:
            resp = requests.request(method, url, timeout=10, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.warning(
                "API request failed (attempt %d/%d): %s",
                attempt, cfg.api_max_retries, exc,
            )
            if attempt < cfg.api_max_retries:
                time.sleep(cfg.api_retry_delay_seconds)
            else:
                raise


def fetch_current_price(
    pair: Optional[str] = None,
    cfg: Optional[StrategyConfig] = None,
) -> float:
    """Return the latest traded price via the trades endpoint."""
    cfg = resolve_cfg(cfg)
    p = pair or cfg.pair
    url = f"{cfg.base_url}/exchange/v1/derivatives/futures/data/trades"
    resp = _request_with_retry("GET", url, cfg=cfg, params={"pair": p})
    trades = resp.json()
    if not trades:
        raise ValueError(f"No trades returned for {p}")
    price = float(trades[-1]["price"])
    _price_cache.append((time.time(), price))
    return price


def fetch_price_x_minutes_ago(
    minutes: int,
    cfg: Optional[StrategyConfig] = None,
) -> Optional[float]:
    """Try to get the price from ~``minutes`` ago (cache then candlestick)."""
    target_ts = time.time() - minutes * 60
    best = _find_closest_cached(target_ts)
    if best is not None:
        return best

    return _fetch_candle_close(minutes, cfg=cfg)


def _find_closest_cached(target_ts: float, tolerance_seconds: float = 120) -> Optional[float]:
    """Search the deque for the entry closest to *target_ts* within tolerance."""
    best_price: Optional[float] = None
    best_diff = float("inf")
    for ts, price in _price_cache:
        diff = abs(ts - target_ts)
        if diff < best_diff:
            best_diff = diff
            best_price = price
    if best_diff <= tolerance_seconds:
        return best_price
    return None


def _fetch_candle_close(
    minutes: int,
    cfg: Optional[StrategyConfig] = None,
) -> float:
    """Fetch a 1-minute candle covering ``minutes`` ago and return its close."""
    cfg = resolve_cfg(cfg)
    now = int(time.time())
    from_ts = now - (minutes + 2) * 60
    to_ts = now

    url = f"{cfg.public_url}/market_data/candlesticks"
    params = {
        "pair": cfg.pair,
        "from": from_ts,
        "to": to_ts,
        "resolution": "1",
        "pcode": "f",
    }
    resp = _request_with_retry("GET", url, cfg=cfg, params=params)
    data = resp.json()

    candles = data.get("data", [])
    if not candles:
        raise ValueError(f"No candle data returned for {cfg.pair} (window={minutes}m)")

    target_epoch_ms = (now - minutes * 60) * 1000
    closest = min(candles, key=lambda c: abs(c["time"] - target_epoch_ms))
    price = float(closest["close"])

    _price_cache.append((closest["time"] / 1000, price))
    return price


_USDT_INR_CACHE_TTL = 60  # seconds

def fetch_usdt_inr_rate(cfg: Optional[StrategyConfig] = None) -> float:
    """Fetch the current USDT/INR exchange rate from CoinDCX spot ticker.

    Results are cached for 60 seconds.
    """
    cfg = resolve_cfg(cfg)
    now = time.time()
    if _usdt_inr_cache["rate"] is not None and (now - _usdt_inr_cache["ts"]) < _USDT_INR_CACHE_TTL:
        return _usdt_inr_cache["rate"]

    url = f"{cfg.base_url}/exchange/ticker"
    resp = _request_with_retry("GET", url, cfg=cfg)
    tickers = resp.json()
    for t in tickers:
        if t.get("market") == "USDTINR":
            rate = float(t["last_price"])
            _usdt_inr_cache["rate"] = rate
            _usdt_inr_cache["ts"] = now
            logger.info("USDT/INR rate: %.2f", rate)
            return rate
    raise ValueError("USDT/INR pair not found in CoinDCX ticker data")


def fetch_instrument_rules(
    pair: str,
    margin_currency: str = "INR",
    cfg: Optional[StrategyConfig] = None,
) -> dict:
    """Fetch and cache instrument constraints from CoinDCX."""
    cfg = resolve_cfg(cfg)
    cache_key = f"{pair}:{margin_currency}"
    if cache_key in _instrument_cache:
        return _instrument_cache[cache_key]

    url = f"{cfg.base_url}/exchange/v1/derivatives/futures/data/instrument"
    resp = _request_with_retry(
        "GET", url, cfg=cfg,
        params={"pair": pair, "margin_currency_short_name": margin_currency},
    )
    data = resp.json()

    instr = data.get("instrument", data) if isinstance(data, dict) else data

    rules = {
        "min_quantity": float(instr.get("min_quantity", 0.001)),
        "quantity_increment": float(instr.get("quantity_increment", 0.001)),
        "price_increment": float(instr.get("price_increment", 0.01)),
        "min_notional": float(instr.get("min_notional", 5)),
        "max_quantity": float(instr.get("max_quantity", 1_000_000)),
        "max_market_order_quantity": float(instr.get("max_market_order_quantity", 100)),
    }

    _instrument_cache[cache_key] = rules
    logger.info("Instrument rules for %s (%s): %s", pair, margin_currency, rules)
    return rules


def snap_quantity(qty: float, rules: dict) -> float:
    """Round *qty* down to the nearest valid increment and clamp to limits."""
    from bot.exchange_precision import snap_quantity as _base_snap

    step = rules["quantity_increment"]
    min_qty = rules["min_quantity"]
    max_qty = rules.get("max_market_order_quantity", rules["max_quantity"])

    qty = _base_snap(qty, step)
    qty = max(qty, min_qty)
    qty = min(qty, max_qty)
    return qty


def clear_instrument_cache() -> None:
    _instrument_cache.clear()


def clear_cache() -> None:
    """Discard all cached prices (call when pair or strategy changes)."""
    _price_cache.clear()
    logger.info("Price cache cleared")


def seed_cache(cfg: Optional[StrategyConfig] = None) -> None:
    """Pre-fill the cache with candles spanning the comparison window."""
    cfg = resolve_cfg(cfg)
    window = cfg.comparison_window_minutes
    now = int(time.time())
    from_ts = now - (window + 5) * 60
    to_ts = now

    url = f"{cfg.public_url}/market_data/candlesticks"
    params = {
        "pair": cfg.pair,
        "from": from_ts,
        "to": to_ts,
        "resolution": "1",
        "pcode": "f",
    }

    try:
        resp = _request_with_retry("GET", url, cfg=cfg, params=params)
        data = resp.json()
        candles = data.get("data", [])
        for c in candles:
            _price_cache.append((c["time"] / 1000, float(c["close"])))
        logger.info("Seeded price cache with %d candles", len(candles))
    except Exception as exc:
        logger.warning("Cache seeding failed — will rely on live fetches: %s", exc)
