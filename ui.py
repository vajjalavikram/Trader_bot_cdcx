"""
Streamlit web UI for the CoinDCX Futures Dip / Rise trading bot.

Run with:
    streamlit run ui.py
"""

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import timedelta
from typing import Optional

import streamlit as st

# ═══════════════════════════════════════════════════════════════════════════
# Page configuration (must be the first Streamlit call)
# ═══════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="CoinDCX Strategy Terminal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════════════════════
# Design system — dark fintech theme
# ═══════════════════════════════════════════════════════════════════════════
_CSS = """
<style>
/* ── Global ────────────────────────────────────────────────────────────── */
.stApp { background: #0B0F19; }
.block-container { padding-top: 1rem; padding-bottom: 0; }
header[data-testid="stHeader"] { background: transparent; }

/* ── Sidebar ───────────────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background: #111827;
    border-right: 1px solid rgba(255,255,255,0.06);
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
    color: #9CA3AF;
    font-size: 0.85rem;
}

/* ── Typography helpers ────────────────────────────────────────────────── */
.t-header {
    background: linear-gradient(135deg, #00D2FF, #3A7BD5);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-size: 1.45rem;
    font-weight: 800;
    margin: 0;
    letter-spacing: -0.02em;
    line-height: 1.3;
}
.t-sub { color: #6B7280; font-size: 0.8rem; margin: 0 0 2px; }
.section-lbl {
    color: #6B7280;
    font-size: 0.67rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    font-weight: 700;
    margin: 12px 0 2px;
}
.hr {
    border: none;
    border-top: 1px solid rgba(255,255,255,0.06);
    margin: 8px 0;
}

/* ── Card (for custom HTML blocks) ─────────────────────────────────────── */
.card {
    background: #111827;
    border-radius: 14px;
    padding: 20px 22px;
    border: 1px solid rgba(255,255,255,0.05);
    margin-bottom: 14px;
    transition: box-shadow 0.25s ease;
}
.card:hover { box-shadow: 0 0 24px rgba(0,210,255,0.05); }

/* ── Metric cards ──────────────────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: #111827;
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 12px;
    padding: 14px 16px;
    transition: box-shadow 0.25s ease;
}
[data-testid="stMetric"]:hover {
    box-shadow: 0 0 20px rgba(0,210,255,0.07);
}
[data-testid="stMetricLabel"] p {
    color: #9CA3AF !important;
    font-size: 0.76rem !important;
}
[data-testid="stMetricValue"] div {
    color: #E5E7EB !important;
}

/* ── Bordered containers (right-panel cards) ───────────────────────────── */
[data-testid="stVerticalBlockBorderWrapper"] {
    background: #111827 !important;
    border-color: rgba(255,255,255,0.05) !important;
    border-radius: 14px !important;
}

/* ── Inputs ─────────────────────────────────────────────────────────────── */
.stTextInput > div > div > input,
.stNumberInput > div > div > input {
    background: #0B0F19 !important;
    color: #E5E7EB !important;
    border-color: rgba(255,255,255,0.08) !important;
    border-radius: 8px !important;
}
.stSelectbox > div > div {
    background: #0B0F19 !important;
    border-color: rgba(255,255,255,0.08) !important;
    border-radius: 8px !important;
}
.stSelectbox > div > div > div { color: #E5E7EB !important; }
label { color: #9CA3AF !important; font-size: 0.82rem !important; }
.stRadio label span p { color: #9CA3AF !important; }
.stCaption { color: #6B7280 !important; }

/* ── Buttons ───────────────────────────────────────────────────────────── */
.stButton > button[data-testid="baseButton-primary"] {
    background: linear-gradient(135deg, #00D2FF, #3A7BD5) !important;
    border: none !important;
    color: #fff !important;
    border-radius: 10px !important;
    font-weight: 700 !important;
    transition: box-shadow 0.2s;
}
.stButton > button[data-testid="baseButton-primary"]:hover {
    box-shadow: 0 0 18px rgba(0,210,255,0.25);
}
.stButton > button[data-testid="baseButton-secondary"] {
    background: transparent !important;
    border: 1px solid rgba(255,255,255,0.12) !important;
    color: #E5E7EB !important;
    border-radius: 10px !important;
}

/* ── Status widget ─────────────────────────────────────────────────────── */
[data-testid="stStatusWidget"] {
    background: #111827 !important;
    border: 1px solid rgba(255,255,255,0.05) !important;
    border-radius: 12px !important;
}

/* ── Expander ──────────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
    background: #111827;
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 14px;
}
[data-testid="stExpander"] summary span p {
    color: #E5E7EB !important;
    font-weight: 600;
}

/* ── Text area (logs) ──────────────────────────────────────────────────── */
[data-testid="stTextArea"] textarea {
    background: #0D1117 !important;
    color: #9CA3AF !important;
    font-family: 'JetBrains Mono','Fira Code','Cascadia Code',monospace !important;
    font-size: 0.74rem !important;
    border: 1px solid rgba(255,255,255,0.04) !important;
    border-radius: 10px !important;
    line-height: 1.55 !important;
}
[data-testid="stTextArea"] label { display: none !important; }

/* ── Alert banners ─────────────────────────────────────────────────────── */
.stAlert { border-radius: 10px !important; }

/* ── Mode badges (HTML) ────────────────────────────────────────────────── */
.mbadge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    vertical-align: middle;
}
.mbadge-sim  { background: rgba(59,130,246,0.15); color: #3B82F6; }
.mbadge-live { background: rgba(239,68,68,0.15);  color: #EF4444; }

/* ── Scrollbar ─────────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #0B0F19; }
::-webkit-scrollbar-thumb { background: #1F2937; border-radius: 3px; }
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════
RUNTIME_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "runtime_config.json"
)
PAIR_OPTIONS = {
    "BTCUSDT": "B-BTC_USDT",
    "ETHUSDT": "B-ETH_USDT",
    "SOLUSDT": "B-SOL_USDT",
}

# ═══════════════════════════════════════════════════════════════════════════
# Shared state helpers (unchanged backend logic)
# ═══════════════════════════════════════════════════════════════════════════

class BotState:
    """Thread-safe container for live bot telemetry."""

    def __init__(self):
        self._lock = threading.Lock()
        self.current_price: Optional[float] = None
        self.past_price: Optional[float] = None
        self.price_change: Optional[float] = None
        self.entry_triggered: bool = False
        self.entry_side: Optional[str] = None
        self.position_status: str = "No position"
        self.phase: str = "Idle"
        self.error: Optional[str] = None
        self.error_detail: Optional[dict] = None
        self.wallet_balance: Optional[float] = None
        self.margin_currency: str = "INR"
        self.usdt_inr_rate: float = 0.0
        self.instrument_rules: Optional[dict] = None
        self.leverage: int = 10
        self.unrealized_pnl: Optional[float] = None
        self.pnl_percent: Optional[float] = None
        self.position_info: Optional[dict] = None
        self.logs: deque = deque(maxlen=500)

    def update(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "current_price": self.current_price,
                "past_price": self.past_price,
                "price_change": self.price_change,
                "entry_triggered": self.entry_triggered,
                "entry_side": self.entry_side,
                "position_status": self.position_status,
                "phase": self.phase,
                "error": self.error,
                "error_detail": self.error_detail,
                "wallet_balance": self.wallet_balance,
                "margin_currency": self.margin_currency,
                "usdt_inr_rate": self.usdt_inr_rate,
                "instrument_rules": self.instrument_rules,
                "leverage": self.leverage,
                "unrealized_pnl": self.unrealized_pnl,
                "pnl_percent": self.pnl_percent,
                "position_info": self.position_info,
                "logs": list(self.logs),
            }

    def reset(self):
        with self._lock:
            self.current_price = None
            self.past_price = None
            self.price_change = None
            self.entry_triggered = False
            self.entry_side = None
            self.position_status = "No position"
            self.phase = "Idle"
            self.error = None
            self.error_detail = None
            self.wallet_balance = None
            self.usdt_inr_rate = 0.0
            self.instrument_rules = None
            self.leverage = 10
            self.unrealized_pnl = None
            self.pnl_percent = None
            self.position_info = None
            self.logs.clear()


class _UILogHandler(logging.Handler):
    """Routes log records into BotState.logs for UI display."""

    def __init__(self, state: BotState):
        super().__init__()
        self.state = state
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        ))

    def emit(self, record):
        try:
            self.state.logs.append(self.format(record))
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# Session-state initialisation
# ═══════════════════════════════════════════════════════════════════════════
if "bot_state" not in st.session_state:
    st.session_state.bot_state = BotState()
if "stop_event" not in st.session_state:
    st.session_state.stop_event = threading.Event()
if "bot_thread" not in st.session_state:
    st.session_state.bot_thread = None
if "instrument_rules" not in st.session_state:
    st.session_state.instrument_rules = None
if "preview_price" not in st.session_state:
    st.session_state.preview_price = None
if "preview_usdt_inr" not in st.session_state:
    st.session_state.preview_usdt_inr = None
if "trading_mode" not in st.session_state:
    st.session_state.trading_mode = "Simulation"


def _is_running() -> bool:
    t = st.session_state.bot_thread
    return t is not None and t.is_alive()


# ═══════════════════════════════════════════════════════════════════════════
# Instrument data helpers
# ═══════════════════════════════════════════════════════════════════════════
def _fetch_instrument_preview(pair_cdx: str, margin_currency: str):
    """Fetch instrument rules and current price; stores in session_state."""
    try:
        from bot.market_data import (
            fetch_instrument_rules, fetch_current_price, fetch_usdt_inr_rate,
        )
        st.session_state.instrument_rules = fetch_instrument_rules(
            pair_cdx, margin_currency,
        )
        st.session_state.preview_price = fetch_current_price(pair_cdx)
        if margin_currency == "INR":
            st.session_state.preview_usdt_inr = fetch_usdt_inr_rate()
        else:
            st.session_state.preview_usdt_inr = None
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# Bot thread target (unchanged)
# ═══════════════════════════════════════════════════════════════════════════
def _bot_thread_target(
    params: dict, stop_event: threading.Event, state: BotState,
):
    handler = _UILogHandler(state)
    bot_logger = logging.getLogger("bot")
    bot_logger.handlers = [
        h for h in bot_logger.handlers if not isinstance(h, _UILogHandler)
    ]
    bot_logger.addHandler(handler)
    bot_logger.setLevel(logging.DEBUG)

    try:
        from bot.config import load_from_dict
        load_from_dict(params)

        sim_wallet = None
        if params.get("trading_mode", "simulation") == "simulation":
            from bot.sim_wallet import SimWallet
            sim_wallet = SimWallet(
                initial_balance=params.get("sim_balance", 10_000.0),
                currency=params.get("margin_currency", "INR"),
            )

        from bot.main import run
        run(
            stop_event=stop_event,
            status_callback=lambda **kw: state.update(**kw),
            sim_wallet=sim_wallet,
        )

        if stop_event.is_set():
            state.update(phase="Stopped")
        elif state.snapshot()["phase"] not in ("Done", "Expired"):
            state.update(phase="Done")
    except Exception as exc:
        state.update(phase="Error", error=str(exc))
        logging.getLogger("bot").error("Bot crashed: %s", exc, exc_info=True)
    finally:
        bot_logger.removeHandler(handler)


# ###########################################################################
#                              UI  LAYOUT
# ###########################################################################

is_running = _is_running()

# ── Header ─────────────────────────────────────────────────────────────────
st.markdown(
    '<p class="t-header">CoinDCX Strategy Terminal</p>'
    '<p class="t-sub">Automated Futures Trading</p>'
    '<hr class="hr">',
    unsafe_allow_html=True,
)

# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        '<div style="text-align:center;padding:4px 0 10px">'
        '<span style="font-size:1rem;font-weight:700;'
        "background:linear-gradient(135deg,#00D2FF,#3A7BD5);"
        '-webkit-background-clip:text;-webkit-text-fill-color:transparent">'
        "Strategy Terminal</span></div>"
        '<hr class="hr">',
        unsafe_allow_html=True,
    )

    # ── Trading mode ──────────────────────────────────────────────────────
    trading_mode = st.radio(
        "Mode",
        ["Simulation", "Live"],
        index=["Simulation", "Live"].index(st.session_state.trading_mode),
        horizontal=True,
        disabled=is_running,
    )
    st.session_state.trading_mode = trading_mode

    sim_balance = 10_000.0
    if trading_mode == "Simulation":
        sim_balance = st.number_input(
            "Sim Balance",
            min_value=100.0,
            max_value=10_000_000.0,
            value=10_000.0,
            step=1000.0,
            disabled=is_running,
        )

    st.markdown('<hr class="hr">', unsafe_allow_html=True)

    # ── Connection ────────────────────────────────────────────────────────
    st.markdown('<p class="section-lbl">Connection</p>', unsafe_allow_html=True)
    api_key = st.text_input(
        "API Key",
        type="password",
        value=os.getenv("COINDCX_API_KEY", ""),
        disabled=is_running,
    )
    api_secret = st.text_input(
        "API Secret",
        type="password",
        value=os.getenv("COINDCX_API_SECRET", ""),
        disabled=is_running,
    )
    if trading_mode == "Simulation":
        st.caption("Optional in Simulation mode")
    else:
        st.caption("Required for Live trading")

    st.markdown('<hr class="hr">', unsafe_allow_html=True)

    # ── Instrument ────────────────────────────────────────────────────────
    st.markdown('<p class="section-lbl">Instrument</p>', unsafe_allow_html=True)
    pair_label = st.selectbox(
        "Pair",
        list(PAIR_OPTIONS.keys()),
        disabled=is_running,
    )
    pair = PAIR_OPTIONS[pair_label]

    margin_currency = st.selectbox(
        "Margin Currency",
        ["INR", "USDT"],
        disabled=is_running,
    )
    currency_symbol = "₹" if margin_currency == "INR" else "$"

    st.markdown('<hr class="hr">', unsafe_allow_html=True)

    # ── Strategy ──────────────────────────────────────────────────────────
    st.markdown('<p class="section-lbl">Strategy</p>', unsafe_allow_html=True)
    dip_percent = st.number_input(
        "Dip %", min_value=0.01, max_value=100.0, value=5.0, step=0.1,
        disabled=is_running,
    )
    comparison_window = st.number_input(
        "Comparison Window (min)", min_value=1, max_value=10080, value=60,
        step=1, disabled=is_running,
    )
    check_frequency = st.number_input(
        "Check Frequency (sec)", min_value=5, max_value=3600, value=30,
        step=5, disabled=is_running,
    )
    strategy_expiry = st.number_input(
        "Strategy Expiry (min)", min_value=1, max_value=43200, value=1440,
        step=10, disabled=is_running,
    )

# ── Fetch instrument data for selected pair ────────────────────────────────
if not is_running:
    _fetch_instrument_preview(pair, margin_currency)

instr = st.session_state.instrument_rules
preview_price = st.session_state.preview_price
preview_usdt_inr = st.session_state.preview_usdt_inr

# ── Mode banner ────────────────────────────────────────────────────────────
if trading_mode == "Simulation":
    st.info("**Simulation Mode** — no real trades will be executed.")
else:
    st.warning("**Live Trading Mode** — real orders will be executed.")

# ── Main two-column layout: dashboard (3) | strategy panel (1) ─────────────
col_main, col_right = st.columns([3, 1], gap="medium")

# ═══════════════════════════════════════════════════════════════════════════
# RIGHT PANEL — trading controls
# ═══════════════════════════════════════════════════════════════════════════
with col_right:

    # ── Position Settings ─────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown(
            '<p class="section-lbl">Position Settings</p>',
            unsafe_allow_html=True,
        )
        leverage = st.number_input(
            "Leverage", min_value=1, max_value=125, value=10, step=1,
            disabled=is_running,
        )

        # Notional input with min/max from instrument rules
        min_notional_val = 5.0
        max_notional_val: Optional[float] = None
        if instr:
            min_notional_val = float(instr["min_notional"])
            if margin_currency == "INR" and preview_usdt_inr:
                min_notional_val = float(instr["min_notional"]) * preview_usdt_inr
            if instr.get("max_notional"):
                max_notional_val = float(instr["max_notional"])
                if margin_currency == "INR" and preview_usdt_inr:
                    max_notional_val = max_notional_val * preview_usdt_inr
        min_notional_val = max(round(min_notional_val, 2), 1.0)

        default_notional = (
            max(min_notional_val * 2, 5000.0)
            if margin_currency == "INR"
            else max(min_notional_val * 2, 100.0)
        )

        notional_kwargs: dict = dict(
            label=f"Notional ({margin_currency})",
            min_value=min_notional_val,
            value=default_notional,
            step=100.0 if margin_currency == "INR" else 10.0,
            disabled=is_running,
            help="Total position value. Margin = Notional / Leverage.",
        )
        if max_notional_val and max_notional_val > min_notional_val:
            notional_kwargs["max_value"] = max_notional_val

        notional_value = st.number_input(**notional_kwargs)

        # Compact trade preview
        if instr and preview_price and leverage > 0:
            n_usdt = notional_value
            if margin_currency == "INR" and preview_usdt_inr and preview_usdt_inr > 0:
                n_usdt = notional_value / preview_usdt_inr

            from bot.exchange_precision import snap_quantity as _base_snap
            est_qty = _base_snap(n_usdt / preview_price, instr["quantity_increment"])
            est_qty = max(est_qty, instr["min_quantity"])
            margin_req_preview = notional_value / leverage

            st.caption(
                f"Qty ≈ {est_qty:.6f} · "
                f"Margin {currency_symbol}{margin_req_preview:,.2f}"
            )

        order_type = st.selectbox(
            "Order Type", ["market", "limit"], disabled=is_running,
        )

    # ── Risk ──────────────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown(
            '<p class="section-lbl">Risk</p>', unsafe_allow_html=True,
        )
        tp_percent = st.number_input(
            "Take Profit %", min_value=0.01, max_value=100.0, value=3.0,
            step=0.1, disabled=is_running,
        )
        sl_percent = st.number_input(
            "Stop Loss %", min_value=0.01, max_value=100.0, value=2.0,
            step=0.1, disabled=is_running,
        )

    # ── Direction ─────────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown(
            '<p class="section-lbl">Direction</p>', unsafe_allow_html=True,
        )
        strategy_mode = st.selectbox(
            "Strategy Mode", ["momentum", "reversal"], disabled=is_running,
        )
        direction = st.selectbox(
            "Direction", ["LONG", "SHORT"], disabled=is_running,
        )

    # ── Action buttons ────────────────────────────────────────────────────
    start_clicked = st.button(
        "Start Bot",
        disabled=is_running,
        use_container_width=True,
        type="primary",
    )
    stop_clicked = st.button(
        "Stop Bot",
        disabled=not is_running,
        use_container_width=True,
    )

# ═══════════════════════════════════════════════════════════════════════════
# Button logic (start / stop)
# ═══════════════════════════════════════════════════════════════════════════
if start_clicked:
    if trading_mode == "Live" and (not api_key or not api_secret):
        st.error(
            "API Key and Secret are required for Live trading. "
            "Fill them in the sidebar."
        )
    else:
        params = {
            "api_key": api_key,
            "api_secret": api_secret,
            "pair": pair,
            "dip_percent": dip_percent,
            "comparison_window_minutes": comparison_window,
            "check_frequency_seconds": check_frequency,
            "strategy_expiry_minutes": strategy_expiry,
            "notional": notional_value,
            "leverage": leverage,
            "order_type": order_type,
            "take_profit_percent": tp_percent,
            "stop_loss_percent": sl_percent,
            "direction": direction,
            "strategy_mode": strategy_mode,
            "margin_currency": margin_currency,
            "trading_mode": trading_mode.lower(),
            "sim_balance": sim_balance,
        }

        safe_params = {
            k: v for k, v in params.items() if k not in ("api_key", "api_secret")
        }
        with open(RUNTIME_CONFIG_PATH, "w") as f:
            json.dump(safe_params, f, indent=2)

        st.session_state.bot_state.reset()
        st.session_state.bot_state.margin_currency = margin_currency
        st.session_state.bot_state.leverage = leverage
        st.session_state.stop_event.clear()

        t = threading.Thread(
            target=_bot_thread_target,
            args=(
                params,
                st.session_state.stop_event,
                st.session_state.bot_state,
            ),
            daemon=True,
        )
        t.start()
        st.session_state.bot_thread = t
        st.rerun()

if stop_clicked:
    st.session_state.stop_event.set()
    st.session_state.bot_state.update(phase="Stopping")
    time.sleep(0.3)
    st.rerun()


# ═══════════════════════════════════════════════════════════════════════════
# CENTER DASHBOARD — auto-refreshing fragment (renders inside col_main)
# ═══════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=timedelta(seconds=3))
def _live_dashboard():
    snap = st.session_state.bot_state.snapshot()
    phase = snap["phase"]
    mode = st.session_state.get("trading_mode", "Simulation")

    mc = snap.get("margin_currency", "INR")
    csym = "₹" if mc == "INR" else "$"
    rate = snap.get("usdt_inr_rate", 0.0) or 0.0
    rules = snap.get("instrument_rules")
    price_usdt = snap["current_price"]

    # ── Status indicator ──────────────────────────────────────────────────
    def _map_status(state: str) -> str:
        valid = {"running", "complete", "error"}
        if state in valid:
            return state
        return {"off": "complete", "active": "running", "idle": "complete"}.get(
            state, "complete",
        )

    phase_labels = {
        "Idle":           ("Idle",                         "complete"),
        "Starting":       ("Starting…",                    "running"),
        "Scanning":       ("Scanning for entry",           "running"),
        "Placing order":  ("Placing order",                "running"),
        "Positioned":     ("Position open — monitoring",   "running"),
        "Stopping":       ("Stopping…",                    "running"),
        "Stopped":        ("Stopped by user",              "complete"),
        "Expired":        ("Expired — no entry found",     "complete"),
        "Done":           ("Completed",                    "complete"),
        "Error":          ("Error",                        "error"),
    }
    label, colour = phase_labels.get(phase, (phase, "complete"))

    badge_cls = "mbadge-sim" if mode == "Simulation" else "mbadge-live"
    badge_txt = "SIM" if mode == "Simulation" else "LIVE"
    st.markdown(
        f'<span class="mbadge {badge_cls}">{badge_txt}</span>',
        unsafe_allow_html=True,
    )
    st.status(f"**Bot:** {label}", state=_map_status(colour))

    if snap["error"]:
        st.error(snap["error"])

    # ── Debug information (API errors) ────────────────────────────────────
    detail = snap.get("error_detail")
    if detail:
        with st.expander("Debug Information", expanded=True):
            if detail.get("status_code"):
                st.markdown(f"**HTTP Status:** `{detail['status_code']}`")
            if detail.get("message"):
                st.code(detail["message"], language="text")
            d1, d2 = st.columns(2)
            with d1:
                st.markdown("**Payload Sent**")
                payload = detail.get("payload")
                if payload:
                    st.json(payload)
                else:
                    st.caption("No payload captured.")
            with d2:
                st.markdown("**Exchange Response**")
                response = detail.get("response")
                if response:
                    st.json(response)
                else:
                    st.caption("No response body captured.")
            entry_params = detail.get("entry_params")
            if entry_params:
                st.markdown("**Entry Parameters**")
                st.json(entry_params)

    # ── Wallet balance card ───────────────────────────────────────────────
    wb = snap["wallet_balance"]
    wallet_label = "Sim Wallet" if mode == "Simulation" else "Wallet Balance"
    bal_str = f"{csym}{wb:,.2f}" if wb is not None else "—"
    st.markdown(
        f'<div class="card">'
        f'<div style="color:#6B7280;font-size:0.75rem">{wallet_label}</div>'
        f'<div style="font-size:2rem;font-weight:700;color:#E5E7EB;'
        f'line-height:1.2">{bal_str}</div>'
        f'<div style="color:#4B5563;font-size:0.7rem;margin-top:2px">'
        f"Available margin · {mc}</div></div>",
        unsafe_allow_html=True,
    )

    # ── Market data cards ─────────────────────────────────────────────────
    m1, m2, m3 = st.columns(3)

    with m1:
        if price_usdt is not None and mc == "INR" and rate > 0:
            st.metric("Current Price", f"₹{price_usdt * rate:,.2f}")
        elif price_usdt is not None:
            st.metric("Current Price", f"${price_usdt:,.4f}")
        else:
            st.metric("Current Price", "—")

    with m2:
        c = snap["price_change"]
        st.metric(
            "Price Change",
            f"{c:+.4f}%" if c is not None else "—",
            delta=f"{c:+.2f}%" if c is not None else None,
        )

    with m3:
        if snap["entry_triggered"]:
            side_txt = (snap["entry_side"] or "").upper()
            st.metric("Entry Trigger", f"Triggered — {side_txt}")
        elif phase == "Scanning":
            st.metric("Entry Trigger", "Waiting…")
        else:
            st.metric("Entry Trigger", "—")

    # ── Position overview (visible when position is active) ───────────────
    pos_info = snap.get("position_info")
    if pos_info:
        st.markdown(
            '<div style="color:#6B7280;font-size:0.67rem;text-transform:uppercase;'
            'letter-spacing:0.12em;font-weight:700;margin:16px 0 6px">'
            "Position Overview</div>",
            unsafe_allow_html=True,
        )

        entry_p = pos_info["entry_price"]
        curr_p = pos_info.get("current_price", 0)
        qty_p = pos_info.get("quantity", 0)
        margin_p = pos_info.get("margin", 0)
        pnl_val = pos_info.get("pnl_value", 0)
        pnl_pct = pos_info.get("pnl_percent", 0)
        pos_side = pos_info.get("side", "")

        # Row 1: prices + size + margin
        p1, p2, p3, p4 = st.columns(4)
        with p1:
            if mc == "INR" and rate > 0:
                st.metric("Entry Price", f"₹{entry_p * rate:,.2f}")
            else:
                st.metric("Entry Price", f"${entry_p:,.4f}")
        with p2:
            if mc == "INR" and rate > 0:
                st.metric("Current Price", f"₹{curr_p * rate:,.2f}")
            else:
                st.metric("Current Price", f"${curr_p:,.4f}")
        with p3:
            st.metric("Position Size", f"{qty_p:.6f}")
        with p4:
            st.metric(f"Margin ({mc})", f"{csym}{margin_p:,.2f}")

        # Row 2: PnL + position label
        q1, q2, q3 = st.columns(3)
        with q1:
            st.metric(
                "Unrealized PnL",
                f"{csym}{pnl_val:+,.2f}",
                delta=f"{pnl_pct:+.2f}%",
            )
        with q2:
            st.metric("PnL %", f"{pnl_pct:+.2f}%")
        with q3:
            pstatus = snap["position_status"]
            st.metric("Position", pstatus[:40] if pstatus else "—")

    elif phase != "Idle":
        st.metric("Position Status", snap["position_status"])


with col_main:
    _live_dashboard()


# ═══════════════════════════════════════════════════════════════════════════
# EVENT FEED — full width, auto-refreshing
# ═══════════════════════════════════════════════════════════════════════════
st.markdown('<hr class="hr">', unsafe_allow_html=True)


@st.fragment(run_every=timedelta(seconds=3))
def _event_feed():
    snap = st.session_state.bot_state.snapshot()
    logs = snap["logs"]
    st.markdown(
        '<p class="section-lbl" style="margin-top:4px">Trading Events</p>',
        unsafe_allow_html=True,
    )
    with st.expander("Event Log", expanded=bool(logs)):
        if logs:
            st.text_area(
                "logs",
                value="\n".join(logs[-100:]),
                height=240,
                disabled=True,
                label_visibility="collapsed",
            )
        else:
            st.caption("No events yet — start the bot to see activity here.")


_event_feed()
