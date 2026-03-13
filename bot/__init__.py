"""CoinDCX futures trading bot package.

Modules
-------
config              Strategy parameters and API key loading.
market_data         Price fetching, candlestick cache, instrument rules.
strategy            Momentum / Reversal entry evaluation.
execution           Order placement and position management via CoinDCX API.
exchange_precision  Price and quantity snapping to exchange tick sizes.
position_monitor    TP/SL monitoring loop for live positions.
sim_wallet          Simulated wallet for paper-trading mode.
strategy_config     Per-strategy configuration dataclass (enables parallel execution).
strategy_manager    Multi-strategy registry, lifecycle, portfolio guardrails.
main                Orchestration — entry scan → order → monitor.
"""
