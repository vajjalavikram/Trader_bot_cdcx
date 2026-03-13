"""
Main entry-point for the CoinDCX Futures Dip/Rise trading bot.

Flow
----
1. Validate configuration.
2. Set leverage for the pair.
3. Seed the price cache.
4. Loop every ``check_frequency_seconds``:
   a. Fetch current price and price from X minutes ago.
   b. Evaluate the strategy (momentum / reversal).
   c. If triggered → place order → begin position monitoring.
   d. If expiry elapses without a trigger → exit.

The ``run()`` function accepts a ``StrategyConfig`` so that multiple
strategies can execute in parallel without sharing global state.
"""

import logging
import sys
import time
import threading
from typing import Callable, Optional

from bot.exchange_precision import snap_price
from bot.execution import (
    get_position, get_wallet_balance, place_order,
    update_leverage, create_tpsl,
)
from bot.market_data import (
    fetch_current_price, fetch_instrument_rules, fetch_price_x_minutes_ago,
    fetch_usdt_inr_rate, seed_cache, clear_cache, snap_quantity,
)
from bot.position_monitor import check_tp_sl_hit, compute_tp_sl, monitor
from bot.sim_wallet import SimWallet, compute_unrealized_pnl
from bot.strategy import evaluate
from bot.strategy_config import StrategyConfig

logger = logging.getLogger(__name__)


class BotError(Exception):
    """Raised for fatal bot errors instead of calling sys.exit."""


def _validate_config(cfg: StrategyConfig) -> None:
    errors = []
    if cfg.trading_mode == "live":
        if not cfg.api_key:
            errors.append("COINDCX_API_KEY is not set")
        if not cfg.api_secret:
            errors.append("COINDCX_API_SECRET is not set")
    if cfg.direction not in ("LONG", "SHORT"):
        errors.append(f"DIRECTION must be LONG or SHORT, got {cfg.direction}")
    if cfg.strategy_mode not in ("momentum", "reversal"):
        errors.append(f"STRATEGY_MODE must be momentum or reversal, got {cfg.strategy_mode}")
    if cfg.order_type not in ("market", "limit"):
        errors.append(f"ORDER_TYPE must be market or limit, got {cfg.order_type}")
    if errors:
        for e in errors:
            logger.error("Config error: %s", e)
        raise BotError("; ".join(errors))


def _compute_quantity(current_price: float, rules: dict, cfg: StrategyConfig) -> float:
    """Derive order quantity from notional value and current price."""
    notional = cfg.notional
    if cfg.margin_currency == "INR":
        usdt_inr = fetch_usdt_inr_rate(cfg=cfg)
        notional_usdt = notional / usdt_inr
        logger.info(
            "INR → USDT notional: ₹%.2f / %.2f = $%.4f",
            notional, usdt_inr, notional_usdt,
        )
    else:
        notional_usdt = notional

    raw_qty = notional_usdt / current_price
    qty = snap_quantity(raw_qty, rules)

    logger.info(
        "Quantity calc: notional=$%.4f  raw=%.8f → snapped=%.8f  (min=%.8f step=%.8f)",
        notional_usdt, raw_qty, qty, rules["min_quantity"], rules["quantity_increment"],
    )
    return qty


def _validate_tp_sl(
    entry_price: float, tp_price: float, sl_price: float, direction: str,
) -> None:
    """Log TP/SL values and warn if they look wrong for the direction."""
    logger.info("--- TP/SL Validation ---")
    logger.info("  Entry price : %.4f", entry_price)
    logger.info("  TP price    : %.4f", tp_price)
    logger.info("  SL price    : %.4f", sl_price)
    logger.info("  Direction   : %s", direction)

    if direction == "LONG":
        if tp_price <= entry_price:
            logger.warning("TP (%.4f) is not above entry (%.4f) for a LONG position", tp_price, entry_price)
        if sl_price >= entry_price:
            logger.warning("SL (%.4f) is not below entry (%.4f) for a LONG position", sl_price, entry_price)
    else:
        if tp_price >= entry_price:
            logger.warning("TP (%.4f) is not below entry (%.4f) for a SHORT position", tp_price, entry_price)
        if sl_price <= entry_price:
            logger.warning("SL (%.4f) is not above entry (%.4f) for a SHORT position", sl_price, entry_price)

    logger.info("  TP/SL validation passed ✓")


_POSITION_POLL_RETRIES = 10
_POSITION_POLL_DELAY = 0.5  # seconds  (total wait ≈ 5 s)


def _resolve_position_id(
    cfg: StrategyConfig,
    stop_event: Optional[threading.Event] = None,
) -> str:
    """Poll for the position until it appears (up to ~5 seconds)."""
    for attempt in range(1, _POSITION_POLL_RETRIES + 1):
        try:
            positions = get_position(cfg=cfg)
            if isinstance(positions, list):
                for pos in positions:
                    if pos.get("pair") == cfg.pair:
                        logger.info(
                            "Position found on attempt %d/%d — id=%s",
                            attempt, _POSITION_POLL_RETRIES, pos["id"],
                        )
                        return pos["id"]
        except Exception as exc:
            logger.warning(
                "Position fetch failed (attempt %d/%d): %s",
                attempt, _POSITION_POLL_RETRIES, exc,
            )

        if attempt < _POSITION_POLL_RETRIES:
            logger.info(
                "Position for %s not yet available — retrying in %.1fs (attempt %d/%d)",
                cfg.pair, _POSITION_POLL_DELAY, attempt, _POSITION_POLL_RETRIES,
            )
            if stop_event:
                stop_event.wait(timeout=_POSITION_POLL_DELAY)
                if stop_event.is_set():
                    raise BotError("Stop requested while waiting for position")
            else:
                time.sleep(_POSITION_POLL_DELAY)

    raise BotError(
        f"Position not found after order execution — "
        f"polled {_POSITION_POLL_RETRIES} times over "
        f"{_POSITION_POLL_RETRIES * _POSITION_POLL_DELAY:.1f}s for {cfg.pair}. "
        f"TP/SL creation skipped."
    )


def _sim_monitor(
    sim_wallet: SimWallet,
    sim_position: dict,
    cfg: StrategyConfig,
    instrument_rules: Optional[dict] = None,
    stop_event: Optional[threading.Event] = None,
    status_callback: Optional[Callable] = None,
) -> str:
    """Monitor a simulated position, checking TP/SL against live market prices."""

    def _status(**kw):
        if status_callback:
            status_callback(**kw)

    def _sleep(seconds: float) -> bool:
        if stop_event:
            stop_event.wait(timeout=seconds)
            return stop_event.is_set()
        time.sleep(seconds)
        return False

    entry_price = sim_position["entry_price"]
    direction = cfg.direction
    price_inc = instrument_rules.get("price_increment", 0) if instrument_rules else 0
    tp_price, sl_price = compute_tp_sl(
        entry_price, direction, price_inc,
        tp_percent=cfg.take_profit_percent,
        sl_percent=cfg.stop_loss_percent,
    )
    position_id = sim_position["id"]
    quantity = sim_position["quantity"]
    margin = sim_position["margin"]

    logger.info(
        "SIM: Monitoring %s — dir=%s entry=%.4f TP=%.4f SL=%.4f",
        position_id, direction, entry_price, tp_price, sl_price,
    )

    while True:
        if _sleep(cfg.check_frequency_seconds):
            logger.info("Stop signal received during sim monitoring")
            return "stopped"

        try:
            current_price = fetch_current_price(cfg=cfg)
        except Exception as exc:
            logger.error("Price fetch failed during sim monitoring: %s", exc)
            continue

        rate = 1.0
        if cfg.margin_currency == "INR":
            try:
                rate = fetch_usdt_inr_rate(cfg=cfg)
            except Exception:
                pass

        pnl = compute_unrealized_pnl(
            sim_position, current_price, cfg.margin_currency, rate,
        )

        _status(
            current_price=current_price,
            usdt_inr_rate=rate,
            unrealized_pnl=pnl["pnl_local"],
            pnl_percent=pnl["pnl_percent"],
            position_info={
                "entry_price": entry_price,
                "current_price": current_price,
                "quantity": quantity,
                "margin": margin,
                "side": direction,
                "pnl_value": pnl["pnl_local"],
                "pnl_percent": pnl["pnl_percent"],
            },
            position_status=(
                f"SIM {direction} @ {entry_price:.4f} | "
                f"Now {current_price:.4f} | "
                f"PnL {pnl['pnl_local']:+.2f} ({pnl['pnl_percent']:+.2f}%) | "
                f"TP {tp_price:.4f} / SL {sl_price:.4f}"
            ),
        )

        logger.debug(
            "SIM tick — price=%.4f PnL=%.2f (%.2f%%)",
            current_price, pnl["pnl_local"], pnl["pnl_percent"],
        )

        hit = check_tp_sl_hit(current_price, tp_price, sl_price, direction)

        if hit:
            logger.info("SIM: %s hit at %.4f", hit.upper(), current_price)
            try:
                sim_wallet.close_position(
                    position_id, current_price, cfg.margin_currency, rate,
                )
            except Exception as exc:
                logger.error("SIM: Failed to close position: %s", exc)

            _status(
                wallet_balance=sim_wallet.get_balance(),
                unrealized_pnl=0.0,
                pnl_percent=0.0,
                position_info=None,
            )
            return hit


def run(
    strategy_config: Optional[StrategyConfig] = None,
    stop_event: Optional[threading.Event] = None,
    status_callback: Optional[Callable] = None,
    sim_wallet: Optional[SimWallet] = None,
) -> None:
    """
    Main bot loop.

    Parameters
    ----------
    strategy_config : StrategyConfig, optional
        Per-strategy configuration.  When ``None`` (CLI mode) a config
        is built from global ``bot.config`` values.
    stop_event : threading.Event, optional
        When set externally the bot exits gracefully at the next check.
    status_callback : callable, optional
        Called with keyword arguments to push live telemetry to the UI.
    sim_wallet : SimWallet, optional
        Simulated wallet for paper-trading mode.
    """
    cfg = strategy_config or StrategyConfig.from_global_config()

    def _status(**kw):
        if status_callback:
            status_callback(**kw)

    def _sleep(seconds: float):
        if stop_event:
            stop_event.wait(timeout=seconds)
            return stop_event.is_set()
        time.sleep(seconds)
        return False

    def _should_stop() -> bool:
        return stop_event is not None and stop_event.is_set()

    # ------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("CoinDCX Futures Dip/Rise Bot starting")
    logger.info("=" * 60)
    logger.info("Pair            : %s", cfg.pair)
    logger.info("Direction       : %s", cfg.direction)
    logger.info("Strategy mode   : %s", cfg.strategy_mode)
    logger.info("Dip/Rise %%      : %.2f%%", cfg.dip_percent)
    logger.info("Window          : %d min", cfg.comparison_window_minutes)
    logger.info("Check frequency : %d sec", cfg.check_frequency_seconds)
    logger.info("Expiry          : %d min", cfg.strategy_expiry_minutes)
    logger.info("Notional        : %.2f %s", cfg.notional, cfg.margin_currency)
    logger.info("Margin (derived): %.2f %s", cfg.margin, cfg.margin_currency)
    logger.info("Leverage        : %dx", cfg.leverage)
    logger.info("Order type      : %s", cfg.order_type)
    logger.info("TP              : %.2f%%", cfg.take_profit_percent)
    logger.info("SL              : %.2f%%", cfg.stop_loss_percent)
    logger.info("Trading mode    : %s", cfg.trading_mode)
    logger.info("=" * 60)

    _validate_config(cfg)
    _status(phase="Starting")

    is_sim = cfg.trading_mode == "simulation"

    # --- Step 1: Set leverage ------------------------------------------------
    if not is_sim:
        try:
            update_leverage(cfg.leverage, cfg=cfg)
        except Exception as exc:
            logger.error("Failed to set leverage: %s", exc)
            raise BotError(f"Leverage update failed: {exc}") from exc
    else:
        logger.info("Simulation mode — skipping leverage update")

    # --- Step 1b: Fetch instrument rules ------------------------------------
    try:
        rules = fetch_instrument_rules(cfg.pair, cfg.margin_currency, cfg=cfg)
        _status(instrument_rules=rules)
        logger.info(
            "Instrument constraints: min_qty=%.8f  step=%.8f  min_notional=%.2f",
            rules["min_quantity"], rules["quantity_increment"], rules["min_notional"],
        )
    except Exception as exc:
        logger.error("Failed to fetch instrument rules: %s", exc)
        raise BotError(f"Instrument rules fetch failed: {exc}") from exc

    # --- Step 1c: Fetch USDT/INR rate if needed -----------------------------
    usdt_inr_rate: float = 0.0
    if cfg.margin_currency == "INR":
        try:
            usdt_inr_rate = fetch_usdt_inr_rate(cfg=cfg)
            _status(usdt_inr_rate=usdt_inr_rate)
        except Exception as exc:
            logger.warning("Could not fetch USDT/INR rate: %s", exc)

    # --- Step 2: Clear stale data & seed cache -------------------------------
    clear_cache()
    seed_cache(cfg=cfg)

    # --- Step 3: Entry scanning loop -----------------------------------------
    start_time = time.time()
    expiry_seconds = cfg.strategy_expiry_minutes * 60
    _status(phase="Scanning")

    while True:
        if _should_stop():
            logger.info("Stop signal received — exiting entry scan.")
            _status(phase="Stopped")
            return

        elapsed = time.time() - start_time
        if elapsed >= expiry_seconds:
            logger.info(
                "Strategy expiry reached (%d min). No entry triggered — shutting down.",
                cfg.strategy_expiry_minutes,
            )
            _status(phase="Expired", position_status="No entry — expired")
            return

        remaining = int((expiry_seconds - elapsed) / 60)
        logger.info("Scanning for entry… (expiry in ~%d min)", remaining)

        try:
            current_price = fetch_current_price(cfg=cfg)
            past_price = fetch_price_x_minutes_ago(cfg.comparison_window_minutes, cfg=cfg)
        except Exception as exc:
            logger.error("Market data error: %s — retrying next cycle", exc)
            _status(current_price=None, price_change=None)
            if _sleep(cfg.check_frequency_seconds):
                _status(phase="Stopped")
                return
            continue

        if past_price is None:
            logger.warning("Could not retrieve past price — skipping cycle")
            _status(current_price=current_price, past_price=None, price_change=None)
            if _sleep(cfg.check_frequency_seconds):
                _status(phase="Stopped")
                return
            continue

        price_change = (current_price - past_price) / past_price * 100

        if cfg.margin_currency == "INR":
            try:
                usdt_inr_rate = fetch_usdt_inr_rate(cfg=cfg)
            except Exception:
                pass

        _status(
            current_price=current_price,
            past_price=past_price,
            price_change=price_change,
            usdt_inr_rate=usdt_inr_rate,
            entry_triggered=False,
        )

        if usdt_inr_rate > 0:
            price_inr = current_price * usdt_inr_rate
            logger.info(
                "Price: $%.2f (₹%s)", current_price, f"{price_inr:,.2f}",
            )
        else:
            logger.info("Price: $%.4f", current_price)

        side = evaluate(current_price, past_price, cfg=cfg)

        if side is not None:
            # --- Entry triggered ------------------------------------------
            _status(entry_triggered=True, entry_side=side, phase="Placing order")

            # ---- Simulation path (early return) -------------------------
            if is_sim:
                if sim_wallet is None:
                    raise BotError("Simulation mode requires a sim_wallet instance")

                sim_bal = sim_wallet.get_balance()
                _status(wallet_balance=sim_bal)
                logger.info("Sim Wallet Balance: %.2f %s", sim_bal, cfg.margin_currency)

                margin_req = cfg.margin
                if margin_req > sim_bal:
                    msg = (
                        f"Insufficient sim {cfg.margin_currency} margin — "
                        f"need {margin_req:.2f}, available {sim_bal:.2f}"
                    )
                    logger.error(msg)
                    _status(phase="Error", error=msg)
                    return

                qty = _compute_quantity(current_price, rules, cfg)

                logger.info("--- Simulated Order ---")
                logger.info("  Side          : %s (%s)", side, cfg.direction)
                logger.info("  Notional      : %.2f %s", cfg.notional, cfg.margin_currency)
                logger.info("  Margin Req    : %.2f %s", margin_req, cfg.margin_currency)
                logger.info("  Quantity      : %.8f", qty)
                logger.info("  Price         : %.4f", current_price)

                try:
                    sim_pos = sim_wallet.open_position(
                        pair=cfg.pair,
                        side=side,
                        entry_price=current_price,
                        quantity=qty,
                        margin=margin_req,
                        leverage=cfg.leverage,
                    )
                except ValueError as exc:
                    logger.error("Simulated order failed: %s", exc)
                    _status(phase="Error", error=str(exc))
                    return

                logger.info(
                    "Simulated order executed — id=%s entry=%.4f qty=%.6f",
                    sim_pos["id"], current_price, qty,
                )

                entry_price = current_price
                tp_price, sl_price = compute_tp_sl(
                    entry_price, cfg.direction, rules["price_increment"],
                    tp_percent=cfg.take_profit_percent,
                    sl_percent=cfg.stop_loss_percent,
                )
                _validate_tp_sl(entry_price, tp_price, sl_price, cfg.direction)

                _status(
                    phase="Positioned",
                    position_status=(
                        f"SIM {cfg.direction} @ {entry_price:.4f}  "
                        f"TP={tp_price:.4f}  SL={sl_price:.4f}"
                    ),
                    wallet_balance=sim_wallet.get_balance(),
                )

                result = _sim_monitor(
                    sim_wallet, sim_pos, cfg,
                    instrument_rules=rules,
                    stop_event=stop_event,
                    status_callback=status_callback,
                )
                logger.info("Simulated position closed — reason: %s", result)
                exit_label = {
                    "tp": "Take-Profit hit (SIM)",
                    "sl": "Stop-Loss hit (SIM)",
                    "stopped": "Stopped by user",
                }
                _status(
                    phase="Done",
                    position_status=exit_label.get(result, result),
                    wallet_balance=sim_wallet.get_balance(),
                    unrealized_pnl=0.0,
                    pnl_percent=0.0,
                    position_info=None,
                )
                return

            # ---- Live trading path ----------------------------------------
            wallet_bal = get_wallet_balance(cfg.margin_currency, cfg=cfg)
            _status(wallet_balance=wallet_bal)
            margin_req = cfg.margin
            if wallet_bal is not None:
                logger.info("Wallet Balance  : %.2f %s", wallet_bal, cfg.margin_currency)
                if margin_req > wallet_bal:
                    msg = (
                        f"Insufficient {cfg.margin_currency} margin — "
                        f"need {margin_req:.2f}, available {wallet_bal:.2f}"
                    )
                    logger.error(msg)
                    _status(phase="Error", error=msg)
                    return
            else:
                logger.warning(
                    "Could not fetch %s wallet balance — proceeding without validation",
                    cfg.margin_currency,
                )

            qty = _compute_quantity(current_price, rules, cfg)

            order_price = current_price
            if cfg.order_type == "limit":
                raw_p = current_price
                order_price = snap_price(current_price, rules["price_increment"])
                if order_price != raw_p:
                    logger.info("Limit price snapped: %.8f → %.8f", raw_p, order_price)

            logger.info("--- Pre-Order Debug ---")
            logger.info("Margin Currency : %s", cfg.margin_currency)
            logger.info("Wallet Balance  : %s", f"{wallet_bal:.2f}" if wallet_bal is not None else "N/A")
            logger.info("Notional        : %.2f %s", cfg.notional, cfg.margin_currency)
            logger.info("Margin Required : %.2f %s", margin_req, cfg.margin_currency)
            logger.info("Quantity        : %.8f", qty)
            logger.info("Min Quantity    : %.8f", rules["min_quantity"])
            logger.info("Qty Increment   : %.8f", rules["quantity_increment"])
            logger.info(
                "Placing %s %s order — qty=%.6f price=%.4f",
                cfg.order_type, side, qty, order_price,
            )

            try:
                order_result = place_order(
                    side=side,
                    current_price=order_price,
                    quantity=qty,
                    order_type=cfg.order_type,
                    leverage=cfg.leverage,
                    cfg=cfg,
                )
            except Exception as exc:
                logger.error("Order placement failed (network/unexpected): %s", exc)
                raise BotError(f"Order placement failed: {exc}") from exc

            if not order_result.get("success"):
                sc = order_result.get("status_code", "?")
                logger.error(
                    "Order rejected by CoinDCX [HTTP %s]: %s",
                    sc, order_result.get("error"),
                )
                _status(
                    phase="Error",
                    error=f"Order placement failed — HTTP {sc}",
                    error_detail={
                        "status_code": sc,
                        "payload": order_result.get("payload"),
                        "response": order_result.get("error"),
                        "message": order_result.get("message"),
                        "entry_params": {
                            "side": side,
                            "direction": cfg.direction,
                            "order_type": cfg.order_type,
                            "current_price": current_price,
                            "quantity": qty,
                            "leverage": cfg.leverage,
                        },
                    },
                )
                return

            entry_price = order_result["entry_price"]
            logger.info(
                "Order accepted — id=%s status=%s entry_price=%.4f qty=%.6f",
                order_result["order_id"], order_result["status"],
                entry_price, order_result["quantity"],
            )

            tp_price, sl_price = compute_tp_sl(
                entry_price, cfg.direction, rules["price_increment"],
                tp_percent=cfg.take_profit_percent,
                sl_percent=cfg.stop_loss_percent,
            )
            _validate_tp_sl(entry_price, tp_price, sl_price, cfg.direction)

            _status(
                phase="Positioned",
                position_status=f"{cfg.direction} @ {entry_price:.4f}  TP={tp_price:.4f}  SL={sl_price:.4f}",
            )

            try:
                position_id = _resolve_position_id(cfg, stop_event=stop_event)
            except BotError as exc:
                logger.error("Could not resolve position ID: %s", exc)
                _status(
                    error=str(exc),
                    error_detail={
                        "message": str(exc),
                        "entry_params": {
                            "pair": cfg.pair,
                            "entry_price": entry_price,
                            "direction": cfg.direction,
                            "tp_price": tp_price,
                            "sl_price": sl_price,
                        },
                    },
                )
                logger.warning("Falling back to client-side monitoring without server TP/SL")
                position_id = None

            if position_id is not None:
                tpsl_result = create_tpsl(
                    position_id, tp_price=tp_price, sl_price=sl_price, cfg=cfg,
                )
                if not tpsl_result.get("success"):
                    sc = tpsl_result.get("status_code", "?")
                    logger.error(
                        "TP/SL creation failed [HTTP %s]: %s",
                        sc, tpsl_result.get("error"),
                    )
                    _status(
                        error=f"TP/SL creation failed — HTTP {sc}  (position is open without server TP/SL)",
                        error_detail={
                            "status_code": sc,
                            "payload": tpsl_result.get("payload"),
                            "response": tpsl_result.get("error"),
                            "message": tpsl_result.get("message"),
                            "entry_params": {
                                "position_id": position_id,
                                "entry_price": entry_price,
                                "tp_price": tp_price,
                                "sl_price": sl_price,
                                "direction": cfg.direction,
                            },
                        },
                    )
                    logger.warning(
                        "Continuing to client-side monitoring despite TP/SL creation failure"
                    )

            result = monitor(
                entry_price,
                position_id,
                cfg=cfg,
                quantity=order_result["quantity"],
                margin=margin_req,
                usdt_inr_rate=usdt_inr_rate,
                stop_event=stop_event,
                status_callback=status_callback,
            )
            logger.info("Position closed — reason: %s", result)
            exit_label = {"tp": "Take-Profit hit", "sl": "Stop-Loss hit", "closed": "Closed server-side", "stopped": "Stopped by user"}
            _status(
                phase="Done",
                position_status=exit_label.get(result, result),
                unrealized_pnl=0.0,
                pnl_percent=0.0,
                position_info=None,
            )
            return

        if _sleep(cfg.check_frequency_seconds):
            _status(phase="Stopped")
            return


if __name__ == "__main__":
    try:
        run()
    except BotError as exc:
        logger.critical("Bot terminated: %s", exc)
        sys.exit(1)
