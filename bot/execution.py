"""
Place orders and manage leverage via the CoinDCX Futures API.

All authenticated requests follow the pattern:
  1. Build a JSON body with a top-level ``timestamp``.
  2. HMAC-SHA256 sign the compact JSON string.
  3. POST with ``X-AUTH-APIKEY`` and ``X-AUTH-SIGNATURE`` headers.
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Optional

import requests

from bot import config
from bot.config import normalize_pair

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exception carrying full API error context
# ---------------------------------------------------------------------------

class CoinDCXAPIError(Exception):
    """Raised when CoinDCX returns a non-200 response.

    Attributes carry every detail needed for debugging in the UI.
    """

    def __init__(
        self,
        endpoint: str,
        status_code: int,
        response_text: str,
        response_json: Any,
        payload: str,
    ):
        self.endpoint = endpoint
        self.status_code = status_code
        self.response_text = response_text
        self.response_json = response_json
        self.payload = payload
        super().__init__(
            f"CoinDCX API error [{status_code}] on {endpoint}: "
            f"{response_text[:300]}"
        )


# ---------------------------------------------------------------------------
# Low-level transport
# ---------------------------------------------------------------------------

def _signed_request(method: str, endpoint: str, body: Dict[str, Any]) -> Any:
    """Sign *body*, send *method* request to *endpoint*, return parsed JSON.

    - Logs the outgoing payload and inbound response at INFO level.
    - 4xx errors are raised immediately (bad request — retrying won't help).
    - 5xx / network errors are retried up to ``API_MAX_RETRIES`` times.
    """
    json_body = json.dumps(body, separators=(",", ":"))
    url = f"{config.BASE_URL}{endpoint}"

    logger.info("CoinDCX request  → %s %s", method, endpoint)
    logger.info("CoinDCX payload  : %s", json_body)

    secret_bytes = bytes(config.API_SECRET, encoding="utf-8")
    signature = hmac.new(secret_bytes, json_body.encode(), hashlib.sha256).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": config.API_KEY,
        "X-AUTH-SIGNATURE": signature,
    }

    for attempt in range(1, config.API_MAX_RETRIES + 1):
        try:
            resp = requests.request(
                method, url, data=json_body, headers=headers, timeout=15,
            )

            logger.info(
                "CoinDCX response ← %s [HTTP %d]: %s",
                endpoint, resp.status_code, resp.text[:1000],
            )

            if resp.status_code == 200:
                return resp.json()

            try:
                resp_json = resp.json()
            except Exception:
                resp_json = {"raw": resp.text}

            logger.error(
                "API error on %s [HTTP %d]:\n"
                "  Response : %s\n"
                "  Payload  : %s",
                endpoint, resp.status_code, resp.text, json_body,
            )

            if 400 <= resp.status_code < 500:
                raise CoinDCXAPIError(
                    endpoint, resp.status_code, resp.text, resp_json, json_body,
                )

            if attempt < config.API_MAX_RETRIES:
                logger.warning("Retrying in %.1fs…", config.API_RETRY_DELAY_SECONDS)
                time.sleep(config.API_RETRY_DELAY_SECONDS)
                continue

            raise CoinDCXAPIError(
                endpoint, resp.status_code, resp.text, resp_json, json_body,
            )

        except CoinDCXAPIError:
            raise

        except requests.RequestException as exc:
            logger.warning(
                "Network error on %s (attempt %d/%d): %s",
                endpoint, attempt, config.API_MAX_RETRIES, exc,
            )
            if attempt < config.API_MAX_RETRIES:
                time.sleep(config.API_RETRY_DELAY_SECONDS)
            else:
                raise


def _sign_and_post(endpoint: str, body: Dict[str, Any]) -> Any:
    return _signed_request("POST", endpoint, body)


def _sign_and_get(endpoint: str, body: Dict[str, Any]) -> Any:
    return _signed_request("GET", endpoint, body)


# ---------------------------------------------------------------------------
# Leverage
# ---------------------------------------------------------------------------

def update_leverage(leverage: int) -> None:
    """Set leverage for the configured pair before placing an order."""
    body: Dict[str, Any] = {
        "timestamp": _ts(),
        "leverage": str(leverage),
        "pair": normalize_pair(config.PAIR),
    }
    if config.MARGIN_CURRENCY != "USDT":
        body["margin_currency_short_name"] = config.MARGIN_CURRENCY
    result = _sign_and_post(
        "/exchange/v1/derivatives/futures/positions/update_leverage", body,
    )
    logger.info("Leverage updated to %dx: %s", leverage, result)


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

def place_order(
    side: str,
    current_price: float,
    quantity: float,
    order_type: str = "market",
    leverage: int = 10,
) -> Dict[str, Any]:
    """
    Place a CoinDCX Futures order (without TP/SL).

    TP/SL must be created separately via :func:`create_tpsl` after the
    position is opened — the order-creation endpoint does not support them.

    Returns a dict that **always** contains a ``success`` boolean.

    On success::

        {"success": True, "order_id": "…", "entry_price": …,
         "quantity": …, "status": "…", "_raw": {…}}

    On API error::

        {"success": False, "status_code": 400, "error": {…},
         "payload": {…}, "message": "…"}
    """
    api_pair = normalize_pair(config.PAIR)
    api_order_type = "market_order" if order_type == "market" else "limit_order"

    order: Dict[str, Any] = {
        "side": side,
        "pair": api_pair,
        "order_type": api_order_type,
        "total_quantity": quantity,
        "leverage": leverage,
        "margin_currency_short_name": config.MARGIN_CURRENCY,
        "notification": "no_notification",
        "hidden": False,
        "post_only": False,
    }

    if api_order_type == "limit_order":
        order["price"] = str(current_price)
        order["time_in_force"] = "good_till_cancel"

    body = {
        "timestamp": _ts(),
        "order": order,
    }

    logger.info("CoinDCX Order Payload:\n%s", json.dumps(body, indent=2))

    # --- Send request (CoinDCXAPIError is caught here, not propagated) ----
    try:
        raw = _sign_and_post(
            "/exchange/v1/derivatives/futures/orders/create", body,
        )
    except CoinDCXAPIError as exc:
        logger.error(
            "Order placement failed [HTTP %d]: %s",
            exc.status_code, exc.response_text,
        )
        return {
            "success": False,
            "status_code": exc.status_code,
            "error": exc.response_json,
            "payload": body,
            "message": str(exc),
        }

    # --- Parse successful response (list of orders) -----------------------
    if isinstance(raw, list):
        order_data = raw[0] if raw else {}
    else:
        order_data = raw if isinstance(raw, dict) else {}

    entry_price = _extract_entry_price(order_data, current_price)

    result: Dict[str, Any] = {
        "success": True,
        "order_id": order_data.get("id"),
        "entry_price": entry_price,
        "quantity": float(order_data.get("total_quantity", quantity)),
        "status": order_data.get("status", "unknown"),
        "_raw": order_data,
    }

    logger.info(
        "Order result: id=%s status=%s entry=%.4f qty=%.6f",
        result["order_id"], result["status"],
        result["entry_price"], result["quantity"],
    )
    return result


def _extract_entry_price(
    order_data: Dict[str, Any], fallback: float,
) -> float:
    """Pick the best available price from the order response.

    Preference: avg_price (filled) → price (limit) → fallback.
    """
    avg = order_data.get("avg_price")
    if avg is not None:
        try:
            val = float(avg)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass

    price = order_data.get("price")
    if price is not None:
        try:
            val = float(price)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass

    return fallback


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def get_position(pair: Optional[str] = None) -> Any:
    """Fetch the current position for the configured pair."""
    body = {
        "timestamp": _ts(),
        "page": "1",
        "size": "10",
        "pairs": pair or normalize_pair(config.PAIR),
        "margin_currency_short_name": [config.MARGIN_CURRENCY],
    }
    return _sign_and_post("/exchange/v1/derivatives/futures/positions", body)


def exit_position(position_id: str) -> Dict[str, Any]:
    """Close an entire position by its ID (market exit)."""
    body = {
        "timestamp": _ts(),
        "id": position_id,
    }
    result = _sign_and_post("/exchange/v1/derivatives/futures/positions/exit", body)
    logger.info("Position exit requested: %s", result)
    return result


def create_tpsl(
    position_id: str,
    tp_price: Optional[float] = None,
    sl_price: Optional[float] = None,
) -> Dict[str, Any]:
    """Attach take-profit and/or stop-loss to an open position.

    Returns a dict with ``success`` boolean so the caller can display
    exchange errors in the UI without crashing.
    """
    logger.info("Creating TP/SL for position: %s", position_id)
    if tp_price is not None:
        logger.info("  TP price: %.4f", tp_price)
    if sl_price is not None:
        logger.info("  SL price: %.4f", sl_price)

    body: Dict[str, Any] = {
        "timestamp": _ts(),
        "id": position_id,
    }
    if tp_price is not None:
        body["take_profit"] = {
            "stop_price": str(tp_price),
            "order_type": "take_profit_market",
        }
    if sl_price is not None:
        body["stop_loss"] = {
            "stop_price": str(sl_price),
            "order_type": "stop_market",
        }

    try:
        result = _sign_and_post(
            "/exchange/v1/derivatives/futures/positions/create_tpsl", body,
        )
        logger.info("TP/SL set successfully on position %s: %s", position_id, result)
        return {"success": True, "_raw": result}
    except CoinDCXAPIError as exc:
        logger.error(
            "TP/SL creation failed [HTTP %d]: %s", exc.status_code, exc.response_text,
        )
        return {
            "success": False,
            "status_code": exc.status_code,
            "error": exc.response_json,
            "payload": body,
            "message": str(exc),
        }


# ---------------------------------------------------------------------------
# Wallet balance
# ---------------------------------------------------------------------------

def get_futures_wallets() -> list:
    """Fetch all futures wallets (INR and USDT) from CoinDCX."""
    body = {"timestamp": _ts()}
    return _sign_and_get("/exchange/v1/derivatives/futures/wallets", body)


def get_wallet_balance(currency: str = "INR") -> Optional[float]:
    """Return available margin for a specific futures wallet currency.

    Available = balance − locked_balance − cross_order_margin − cross_user_margin.
    Returns ``None`` when the wallet for *currency* is not found.
    """
    try:
        wallets = get_futures_wallets()
    except Exception as exc:
        logger.warning("Could not fetch futures wallets: %s", exc)
        return None

    if not isinstance(wallets, list):
        return None

    target = currency.upper()
    for w in wallets:
        if w.get("currency_short_name") == target:
            balance = float(w.get("balance", 0))
            locked = float(w.get("locked_balance", 0))
            cross_order = float(w.get("cross_order_margin", 0))
            cross_user = float(w.get("cross_user_margin", 0))
            available = balance - locked - cross_order - cross_user
            available = max(available, 0.0)

            logger.info("--- %s Wallet Breakdown ---", target)
            logger.info("  Currency              : %s", target)
            logger.info("  Balance               : %.2f", balance)
            logger.info("  Locked Margin         : %.2f", locked)
            logger.info("  Cross Order Margin    : %.2f", cross_order)
            logger.info("  Cross Position Margin : %.2f", cross_user)
            logger.info("  Available Margin      : %.2f", available)
            return available

    logger.warning("No %s futures wallet found", target)
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts() -> int:
    return int(round(time.time() * 1000))
