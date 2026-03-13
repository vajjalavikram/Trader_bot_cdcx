"""
Per-strategy configuration dataclass.

Each running strategy carries its own ``StrategyConfig`` instance so that
multiple strategies can execute in parallel without sharing or corrupting
global module-level state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StrategyConfig:
    """Immutable snapshot of every parameter a single strategy needs."""

    # API credentials
    api_key: str = ""
    api_secret: str = ""
    base_url: str = "https://api.coindcx.com"
    public_url: str = "https://public.coindcx.com"

    # Pair / instrument
    pair: str = "B-BTC_USDT"
    margin_currency: str = "INR"

    # Strategy entry
    direction: str = "LONG"
    strategy_mode: str = "momentum"
    dip_percent: float = 5.0
    comparison_window_minutes: int = 60
    check_frequency_seconds: int = 30
    strategy_expiry_minutes: int = 1440

    # Position sizing
    notional: float = 0.0
    margin: float = 0.0
    leverage: int = 10
    order_type: str = "market"

    # Risk management
    take_profit_percent: float = 3.0
    stop_loss_percent: float = 2.0

    # Mode
    trading_mode: str = "simulation"
    sim_balance: float = 10000.0

    # Retry / resilience
    api_max_retries: int = 3
    api_retry_delay_seconds: float = 2.0

    @classmethod
    def from_params(cls, params: dict) -> StrategyConfig:
        """Build from a UI params dictionary."""
        from bot import config as _defaults
        from bot.config import normalize_pair

        pair = normalize_pair(str(params.get("pair", "B-BTC_USDT")))
        notional = float(params.get("notional", 0))
        leverage = max(int(params.get("leverage", 10)), 1)
        margin = float(params.get("margin", 0))

        if notional > 0:
            margin = notional / leverage
        elif margin > 0:
            notional = margin * leverage

        return cls(
            api_key=str(params.get("api_key", _defaults.API_KEY)),
            api_secret=str(params.get("api_secret", _defaults.API_SECRET)),
            base_url=_defaults.BASE_URL,
            public_url=_defaults.PUBLIC_URL,
            pair=pair,
            margin_currency=str(params.get("margin_currency", "INR")).upper(),
            direction=str(params.get("direction", "LONG")).upper(),
            strategy_mode=str(params.get("strategy_mode", "momentum")).lower(),
            dip_percent=float(params.get("dip_percent", 5.0)),
            comparison_window_minutes=int(params.get("comparison_window_minutes", 60)),
            check_frequency_seconds=int(params.get("check_frequency_seconds", 30)),
            strategy_expiry_minutes=int(params.get("strategy_expiry_minutes", 1440)),
            notional=notional,
            margin=margin,
            leverage=leverage,
            order_type=str(params.get("order_type", "market")),
            take_profit_percent=float(params.get("take_profit_percent", 3.0)),
            stop_loss_percent=float(params.get("stop_loss_percent", 2.0)),
            trading_mode=str(params.get("trading_mode", "simulation")).lower(),
            sim_balance=float(params.get("sim_balance", 10000.0)),
            api_max_retries=_defaults.API_MAX_RETRIES,
            api_retry_delay_seconds=_defaults.API_RETRY_DELAY_SECONDS,
        )

    @classmethod
    def from_global_config(cls) -> StrategyConfig:
        """Build from current global ``bot.config`` values (for CLI use)."""
        from bot import config as c
        return cls(
            api_key=c.API_KEY,
            api_secret=c.API_SECRET,
            base_url=c.BASE_URL,
            public_url=c.PUBLIC_URL,
            pair=c.PAIR,
            margin_currency=c.MARGIN_CURRENCY,
            direction=c.DIRECTION,
            strategy_mode=c.STRATEGY_MODE,
            dip_percent=c.DIP_PERCENT,
            comparison_window_minutes=c.COMPARISON_WINDOW_MINUTES,
            check_frequency_seconds=c.CHECK_FREQUENCY_SECONDS,
            strategy_expiry_minutes=c.STRATEGY_EXPIRY_MINUTES,
            notional=c.NOTIONAL,
            margin=c.MARGIN,
            leverage=c.LEVERAGE,
            order_type=c.ORDER_TYPE,
            take_profit_percent=c.TAKE_PROFIT_PERCENT,
            stop_loss_percent=c.STOP_LOSS_PERCENT,
            trading_mode=c.TRADING_MODE,
            sim_balance=c.SIM_BALANCE,
            api_max_retries=c.API_MAX_RETRIES,
            api_retry_delay_seconds=c.API_RETRY_DELAY_SECONDS,
        )


def resolve_cfg(cfg: Optional[StrategyConfig] = None) -> StrategyConfig:
    """Return *cfg* if provided, otherwise build one from global config.

    Used as a compatibility shim so functions can be called both from
    strategy threads (with an explicit cfg) and from CLI/preview contexts
    (without one).
    """
    if cfg is not None:
        return cfg
    return StrategyConfig.from_global_config()
