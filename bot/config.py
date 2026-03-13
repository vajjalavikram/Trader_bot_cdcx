"""
config.py

Central configuration for the trading bot.  All strategy parameters,
API credentials, and runtime tunables are defined here as module-level
variables that can be overridden at startup via environment variables,
a JSON file (``runtime_config.json``), or the Streamlit UI (which calls
``load_from_dict``).

Key relationships
-----------------
* ``NOTIONAL`` is the primary position-size input (total position value).
* ``MARGIN`` is derived: ``NOTIONAL / LEVERAGE``.
* ``PAIR`` is always normalised to CoinDCX's ``B-BASE_QUOTE`` format.
"""

import json as _json
import logging
import os

# ---------------------------------------------------------------------------
# CoinDCX API credentials
# ---------------------------------------------------------------------------
API_KEY: str = os.getenv("COINDCX_API_KEY", "")
API_SECRET: str = os.getenv("COINDCX_API_SECRET", "")

BASE_URL = "https://api.coindcx.com"
PUBLIC_URL = "https://public.coindcx.com"

# ---------------------------------------------------------------------------
# Strategy parameters — override via environment or edit defaults here
# ---------------------------------------------------------------------------
PAIR = os.getenv("PAIR", "B-BTC_USDT")  # normalized below after helper is defined
DIP_PERCENT = float(os.getenv("DIP_PERCENT", "5"))
COMPARISON_WINDOW_MINUTES = int(os.getenv("COMPARISON_WINDOW_MINUTES", "60"))
CHECK_FREQUENCY_SECONDS = int(os.getenv("CHECK_FREQUENCY_SECONDS", "30"))
STRATEGY_EXPIRY_MINUTES = int(os.getenv("STRATEGY_EXPIRY_MINUTES", "1440"))

MARGIN = float(os.getenv("MARGIN", os.getenv("MARGIN_USDT", "100")))
NOTIONAL = float(os.getenv("NOTIONAL", "0"))
LEVERAGE = int(os.getenv("LEVERAGE", "10"))
ORDER_TYPE = os.getenv("ORDER_TYPE", "market")

TAKE_PROFIT_PERCENT = float(os.getenv("TAKE_PROFIT_PERCENT", "3"))
STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "2"))

DIRECTION = os.getenv("DIRECTION", "LONG").upper()
STRATEGY_MODE = os.getenv("STRATEGY_MODE", "momentum").lower()

MARGIN_CURRENCY = os.getenv("MARGIN_CURRENCY", "INR")

TRADING_MODE = os.getenv("TRADING_MODE", "simulation").lower()
SIM_BALANCE = float(os.getenv("SIM_BALANCE", "10000"))

# Portfolio-level risk guardrail (multi-strategy mode)
MAX_PORTFOLIO_MARGIN = float(os.getenv("MAX_PORTFOLIO_MARGIN", "50000"))

# ---------------------------------------------------------------------------
# Retry / resilience
# ---------------------------------------------------------------------------
API_MAX_RETRIES = int(os.getenv("API_MAX_RETRIES", "3"))
API_RETRY_DELAY_SECONDS = float(os.getenv("API_RETRY_DELAY_SECONDS", "2"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ---------------------------------------------------------------------------
# Runtime configuration helpers (used by the Streamlit UI and run.py)
# ---------------------------------------------------------------------------
def normalize_pair(raw: str) -> str:
    """
    Convert user-friendly pair formats into the CoinDCX instrument format.

    Accepted inputs        → Output
    B-BTC_USDT             → B-BTC_USDT   (already correct)
    BTC_USDT               → B-BTC_USDT
    BTC/USDT               → B-BTC_USDT
    BTCUSDT                → B-BTC_USDT
    """
    pair = raw.strip().upper()
    if pair.startswith("B-"):
        return pair

    pair = pair.replace("/", "")

    if "_" in pair:
        base, quote = pair.split("_", 1)
        return f"B-{base}_{quote}"

    for quote in ("USDT", "USDC", "BUSD", "INR", "BTC", "ETH"):
        if pair.endswith(quote) and len(pair) > len(quote):
            base = pair[: -len(quote)]
            return f"B-{base}_{quote}"

    return f"B-{pair}"


# Apply normalisation to the initial value loaded from env
PAIR = normalize_pair(PAIR)


_KEY_MAP = {
    "api_key": "API_KEY",
    "api_secret": "API_SECRET",
    "pair": "PAIR",
    "dip_percent": "DIP_PERCENT",
    "comparison_window_minutes": "COMPARISON_WINDOW_MINUTES",
    "check_frequency_seconds": "CHECK_FREQUENCY_SECONDS",
    "strategy_expiry_minutes": "STRATEGY_EXPIRY_MINUTES",
    "margin": "MARGIN",
    "margin_usdt": "MARGIN",
    "notional": "NOTIONAL",
    "leverage": "LEVERAGE",
    "order_type": "ORDER_TYPE",
    "take_profit_percent": "TAKE_PROFIT_PERCENT",
    "stop_loss_percent": "STOP_LOSS_PERCENT",
    "direction": "DIRECTION",
    "strategy_mode": "STRATEGY_MODE",
    "margin_currency": "MARGIN_CURRENCY",
    "trading_mode": "TRADING_MODE",
    "sim_balance": "SIM_BALANCE",
    "max_portfolio_margin": "MAX_PORTFOLIO_MARGIN",
}

_TYPE_COERCIONS = {
    "DIP_PERCENT": float,
    "COMPARISON_WINDOW_MINUTES": int,
    "CHECK_FREQUENCY_SECONDS": int,
    "STRATEGY_EXPIRY_MINUTES": int,
    "MARGIN": float,
    "NOTIONAL": float,
    "LEVERAGE": int,
    "TAKE_PROFIT_PERCENT": float,
    "STOP_LOSS_PERCENT": float,
    "SIM_BALANCE": float,
    "MAX_PORTFOLIO_MARGIN": float,
}


def load_from_dict(params: dict) -> None:
    """Override module-level config variables from a dictionary."""
    g = globals()
    for json_key, config_key in _KEY_MAP.items():
        if json_key in params:
            g[config_key] = params[json_key]

    for config_key, coerce in _TYPE_COERCIONS.items():
        g[config_key] = coerce(g[config_key])

    g["PAIR"] = normalize_pair(str(g["PAIR"]))
    g["DIRECTION"] = str(g["DIRECTION"]).upper()
    g["STRATEGY_MODE"] = str(g["STRATEGY_MODE"]).lower()
    g["MARGIN_CURRENCY"] = str(g["MARGIN_CURRENCY"]).upper()
    g["TRADING_MODE"] = str(g.get("TRADING_MODE", "simulation")).lower()

    # Sync NOTIONAL ↔ MARGIN: notional is the primary input; margin is derived
    if g["NOTIONAL"] > 0:
        g["MARGIN"] = g["NOTIONAL"] / max(g["LEVERAGE"], 1)
    elif g["MARGIN"] > 0:
        g["NOTIONAL"] = g["MARGIN"] * g["LEVERAGE"]


def load_from_runtime_config(path: str = "runtime_config.json") -> None:
    """Load parameters from a JSON file and apply them."""
    with open(path) as f:
        params = _json.load(f)
    load_from_dict(params)
