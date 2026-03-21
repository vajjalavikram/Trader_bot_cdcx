"""
Streamlit web UI for the CoinDCX Futures multi-strategy trading terminal.

Run with:
    streamlit run ui.py
"""

import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

_BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

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
.stApp { background: #0B0F19; }
.block-container { padding-top: 1rem; padding-bottom: 0; }
header[data-testid="stHeader"] { background: transparent; }

section[data-testid="stSidebar"] {
    background: #111827;
    border-right: 1px solid rgba(255,255,255,0.06);
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
    color: #9CA3AF; font-size: 0.85rem;
}

.t-header {
    background: linear-gradient(135deg, #00D2FF, #3A7BD5);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-size: 1.45rem; font-weight: 800; margin: 0;
    letter-spacing: -0.02em; line-height: 1.3;
}
.t-sub { color: #6B7280; font-size: 0.8rem; margin: 0 0 2px; }
.section-lbl {
    color: #6B7280; font-size: 0.67rem; text-transform: uppercase;
    letter-spacing: 0.12em; font-weight: 700; margin: 12px 0 2px;
}
.hr { border: none; border-top: 1px solid rgba(255,255,255,0.06); margin: 8px 0; }

.card {
    background: #111827; border-radius: 14px; padding: 20px 22px;
    border: 1px solid rgba(255,255,255,0.05); margin-bottom: 14px;
    transition: box-shadow 0.25s ease;
}
.card:hover { box-shadow: 0 0 24px rgba(0,210,255,0.05); }

[data-testid="stMetric"] {
    background: #111827; border: 1px solid rgba(255,255,255,0.05);
    border-radius: 12px; padding: 14px 16px; transition: box-shadow 0.25s ease;
}
[data-testid="stMetric"]:hover { box-shadow: 0 0 20px rgba(0,210,255,0.07); }
[data-testid="stMetricLabel"] p { color: #9CA3AF !important; font-size: 0.76rem !important; }
[data-testid="stMetricValue"] div { color: #E5E7EB !important; }

[data-testid="stVerticalBlockBorderWrapper"] {
    background: #111827 !important;
    border-color: rgba(255,255,255,0.05) !important;
    border-radius: 14px !important;
}

.stTextInput > div > div > input,
.stNumberInput > div > div > input {
    background: #0B0F19 !important; color: #E5E7EB !important;
    border-color: rgba(255,255,255,0.08) !important; border-radius: 8px !important;
}
.stSelectbox > div > div {
    background: #0B0F19 !important;
    border-color: rgba(255,255,255,0.08) !important; border-radius: 8px !important;
}
.stSelectbox > div > div > div { color: #E5E7EB !important; }
label { color: #9CA3AF !important; font-size: 0.82rem !important; }
.stRadio label span p { color: #9CA3AF !important; }
.stCaption { color: #6B7280 !important; }

.stButton > button[data-testid="baseButton-primary"] {
    background: linear-gradient(135deg, #00D2FF, #3A7BD5) !important;
    border: none !important; color: #fff !important;
    border-radius: 10px !important; font-weight: 700 !important;
}
.stButton > button[data-testid="baseButton-primary"]:hover {
    box-shadow: 0 0 18px rgba(0,210,255,0.25);
}
.stButton > button[data-testid="baseButton-secondary"] {
    background: transparent !important;
    border: 1px solid rgba(255,255,255,0.12) !important;
    color: #E5E7EB !important; border-radius: 10px !important;
}

[data-testid="stExpander"] {
    background: #111827; border: 1px solid rgba(255,255,255,0.05);
    border-radius: 14px;
}
[data-testid="stExpander"] summary span p { color: #E5E7EB !important; font-weight: 600; }

[data-testid="stTextArea"] textarea {
    background: #0D1117 !important; color: #9CA3AF !important;
    font-family: 'JetBrains Mono','Fira Code',monospace !important;
    font-size: 0.74rem !important;
    border: 1px solid rgba(255,255,255,0.04) !important;
    border-radius: 10px !important; line-height: 1.55 !important;
}
[data-testid="stTextArea"] label { display: none !important; }

.stAlert { border-radius: 10px !important; }
.mbadge {
    display: inline-block; padding: 3px 10px; border-radius: 20px;
    font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em;
}
.mbadge-sim  { background: rgba(59,130,246,0.15); color: #3B82F6; }
.mbadge-live { background: rgba(239,68,68,0.15);  color: #EF4444; }

/* ── Status bar ────────────────────────────────────────────────────────── */
.status-bar {
    background: #111827; border-radius: 10px; padding: 10px 18px;
    border: 1px solid rgba(255,255,255,0.05); margin-bottom: 12px;
    display: flex; align-items: center; gap: 24px;
    font-size: 0.82rem; color: #9CA3AF;
}
.status-bar .status-dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    margin-right: 6px; vertical-align: middle;
}
.status-bar .status-item { white-space: nowrap; }
.status-bar .status-val { color: #E5E7EB; font-weight: 600; }
.status-bar .status-divider { color: rgba(255,255,255,0.1); }

/* ── Strategy table ────────────────────────────────────────────────────── */
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
[data-testid="stDataFrame"] table { font-size: 0.82rem; }

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
# Session-state initialisation
# ═══════════════════════════════════════════════════════════════════════════
if "strategy_manager" not in st.session_state:
    from backend.client import BackendClient
    st.session_state.strategy_manager = BackendClient(_BACKEND_URL)
if "instrument_rules" not in st.session_state:
    st.session_state.instrument_rules = None
if "preview_price" not in st.session_state:
    st.session_state.preview_price = None
if "preview_usdt_inr" not in st.session_state:
    st.session_state.preview_usdt_inr = None
if "trading_mode" not in st.session_state:
    st.session_state.trading_mode = "Simulation"
if "live_trading_enabled" not in st.session_state:
    st.session_state.live_trading_enabled = False
if "connected" not in st.session_state:
    st.session_state.connected = False

mgr = st.session_state.strategy_manager

# ── Backend health check (non-blocking) ──────────────────────────────────
try:
    _backend_ok = mgr.health_check()
except Exception:
    _backend_ok = False
if not _backend_ok:
    st.warning(
        "Backend server is not reachable. "
        "Start it with: uvicorn backend.main:app --port 8000"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════
def _fetch_instrument_preview(pair_cdx: str, margin_currency: str):
    try:
        from bot.market_data import (
            fetch_instrument_rules, fetch_current_price, fetch_usdt_inr_rate,
        )
        st.session_state.instrument_rules = fetch_instrument_rules(pair_cdx, margin_currency)
        st.session_state.preview_price = fetch_current_price(pair_cdx)
        if margin_currency == "INR":
            st.session_state.preview_usdt_inr = fetch_usdt_inr_rate()
        else:
            st.session_state.preview_usdt_inr = None
    except Exception:
        pass


def _fmt_duration(seconds: float) -> str:
    """Format seconds into a compact human-readable duration."""
    if seconds < 0:
        return "—"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    hours = seconds / 3600
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


_STATUS_LABELS = {
    "waiting":       "Waiting",
    "queued":        "Queued",
    "starting":      "Starting",
    "scanning":      "Scanning",
    "order_placed":  "Order Placed",
    "position_open": "Open",
    "stopping":      "Stopping",
    "stopped":       "Stopped",
    "expired":       "Expired",
    "closed":        "Closed",
    "error":         "Error",
}


def _fmt_price(val, fallback: str = "Scanning") -> str:
    if val is None:
        return fallback
    return f"{val:.4f}"


def _fmt_pnl(val: float, csym: str) -> str:
    if val == 0:
        return "—"
    return f"{csym}{val:+,.2f}"


def _strategy_name(direction: str, strategy_mode: str) -> str:
    """Build a human-readable strategy label like 'Buy Dip' or 'Sell Rise'."""
    if strategy_mode == "reversal":
        return "Buy Dip" if direction == "LONG" else "Sell Rise"
    return "Buy Rise" if direction == "LONG" else "Sell Dip"


# ═══════════════════════════════════════════════════════════════════════════
# Market Viewer — cached data fetchers
# ═══════════════════════════════════════════════════════════════════════════
_MV_BASE_URL = "https://api.coindcx.com"
_MV_PUBLIC_URL = "https://public.coindcx.com"

_MV_INTERVAL_SECONDS = {"1": 60, "5": 300, "60": 3600, "1D": 86400}
_MV_INTERVAL_LABELS = {"1": "1 min", "5": "5 min", "60": "1 hour", "1D": "1 day"}


@st.cache_data(ttl=2)
def _mv_fetch_trades(instrument: str):
    resp = requests.get(
        f"{_MV_BASE_URL}/exchange/v1/derivatives/futures/data/trades",
        params={"pair": instrument}, timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=2)
def _mv_fetch_candles(instrument: str, resolution: str, from_ts: int, to_ts: int):
    resp = requests.get(
        f"{_MV_PUBLIC_URL}/market_data/candlesticks",
        params={
            "pair": instrument, "resolution": resolution,
            "from": from_ts, "to": to_ts, "pcode": "f",
        },
        timeout=5,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", data if isinstance(data, list) else [])


@st.cache_data(ttl=2)
def _mv_fetch_orderbook(instrument: str):
    resp = requests.get(
        f"{_MV_PUBLIC_URL}/market_data/v3/orderbook/{instrument}-futures/50",
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


def _parse_ob_entries(raw) -> list[dict]:
    """Normalise orderbook entries into [{Price, Quantity}] dicts."""
    rows = []
    for entry in raw:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            rows.append({"Price": float(entry[0]), "Quantity": float(entry[1])})
        elif isinstance(entry, dict):
            rows.append({
                "Price": float(entry.get("price", entry.get("rate", 0))),
                "Quantity": float(entry.get("quantity", entry.get("amount", 0))),
            })
    return rows


# ###########################################################################
#                              UI  LAYOUT
# ###########################################################################

st.markdown(
    '<p class="t-header">CoinDCX Strategy Terminal</p>'
    '<p class="t-sub">Multi-Strategy Automated Futures Trading</p>'
    '<hr class="hr">',
    unsafe_allow_html=True,
)

# ═══════════════════════════════════════════════════════════════════════════
# Sidebar
# ═══════════════════════════════════════════════════════════════════════════
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

    trading_mode = st.radio(
        "Mode", ["Simulation", "Live"],
        index=["Simulation", "Live"].index(st.session_state.trading_mode),
        horizontal=True,
    )
    st.session_state.trading_mode = trading_mode

    sim_balance = 10_000.0
    if trading_mode == "Simulation":
        sim_balance = st.number_input(
            "Sim Balance", min_value=100.0, max_value=10_000_000.0,
            value=10_000.0, step=1000.0,
        )
    st.markdown('<hr class="hr">', unsafe_allow_html=True)

    api_key = st.session_state.get(
        "_backend_api_key", os.getenv("COINDCX_API_KEY", ""),
    )
    api_secret = st.session_state.get(
        "_backend_api_secret", os.getenv("COINDCX_API_SECRET", ""),
    )

    _connected = st.session_state.connected
    with st.expander(
        "Backend Connection",
        expanded=not _connected,
    ):
        if _connected:
            st.success("Connected — backend session active.")
        else:
            st.caption(
                "Connect your CoinDCX API key to load strategies."
            )
        _in_key = st.text_input(
            "CoinDCX API Key", type="password",
            value=api_key, key="live_key",
        )
        _in_secret = st.text_input(
            "CoinDCX API Secret", type="password",
            value=api_secret, key="live_secret",
        )
        _remember = st.checkbox(
            "Remember API Secret (encrypted)", key="live_remember",
        )

        if not _connected:
            if st.button(
                "Connect", type="primary",
                use_container_width=True, key="live_connect",
            ):
                if not _in_key or not _in_secret:
                    st.error("Both API Key and API Secret are required.")
                else:
                    _result = mgr.load_session(
                        api_key=_in_key,
                        secret=_in_secret,
                        remember_secret=_remember,
                    )
                    if _result and _result.get("user_id"):
                        mgr.set_api_key(_in_key)
                        st.session_state["_backend_api_key"] = _in_key
                        st.session_state["_backend_api_secret"] = _in_secret
                        st.session_state.connected = True
                        st.session_state.live_trading_enabled = True
                        st.rerun()
                    else:
                        err_detail = (_result or {}).get("error", "")
                        if err_detail:
                            st.error(f"Connection failed: {err_detail}")
                        else:
                            st.error(
                                "Could not connect to backend. "
                                "Check your keys and try again."
                            )

        api_key = _in_key
        api_secret = _in_secret
    st.markdown('<hr class="hr">', unsafe_allow_html=True)

    st.markdown('<p class="section-lbl">Instrument</p>', unsafe_allow_html=True)
    pair_label = st.selectbox("Pair", list(PAIR_OPTIONS.keys()))
    pair = PAIR_OPTIONS[pair_label]
    margin_currency = st.selectbox("Margin Currency", ["INR", "USDT"])
    currency_symbol = "₹" if margin_currency == "INR" else "$"
    st.markdown('<hr class="hr">', unsafe_allow_html=True)

    st.markdown('<p class="section-lbl">Strategy</p>', unsafe_allow_html=True)
    dip_percent = st.number_input("Dip %", min_value=0.01, max_value=100.0, value=5.0, step=0.1)
    comparison_window = st.number_input("Comparison Window (min)", min_value=1, max_value=10080, value=60, step=1)
    check_frequency = st.number_input("Check Frequency (sec)", min_value=5, max_value=3600, value=30, step=5)
    strategy_expiry = st.number_input("Strategy Expiry (min)", min_value=1, max_value=43200, value=1440, step=10)
    st.markdown('<hr class="hr">', unsafe_allow_html=True)

    st.markdown('<p class="section-lbl">System Logs</p>', unsafe_allow_html=True)
    with st.expander("Logs", expanded=False):
        if "logs_cache" not in st.session_state:
            st.session_state.logs_cache = []
        if st.session_state.connected:
            try:
                _sidebar_logs = list(mgr.logs)
            except Exception:
                _sidebar_logs = []
            if _sidebar_logs:
                st.session_state.logs_cache = _sidebar_logs
        _display_logs = st.session_state.logs_cache
        if _display_logs:
            st.text_area("log_output", value="\n".join(_display_logs[-150:]), height=300,
                         disabled=True, label_visibility="collapsed")
        else:
            st.caption("No events yet.")


# ── Fetch instrument data ───────────────────────────────────────────────
_fetch_instrument_preview(pair, margin_currency)

instr = st.session_state.instrument_rules
preview_price = st.session_state.preview_price
preview_usdt_inr = st.session_state.preview_usdt_inr

# ── System status bar (auto-refreshing) ──────────────────────────────────
@st.fragment(run_every=timedelta(seconds=10))
def _system_status_bar():
    _fallback_summary = {
        "active_strategies": 0, "total_pnl": 0.0,
        "margin_used": 0.0, "margin_available": 0.0,
    }
    if st.session_state.connected:
        try:
            summary = mgr.get_portfolio_summary() or _fallback_summary
        except Exception:
            summary = _fallback_summary
        try:
            all_strats = mgr.get_all_strategies() or []
        except Exception:
            all_strats = []
    else:
        summary = _fallback_summary
        all_strats = []

    csym = "₹" if margin_currency == "INR" else "$"
    mode = st.session_state.trading_mode
    active = summary.get("active_strategies", 0)
    has_error = any(s.get("status") == "error" for s in all_strats)

    if has_error:
        dot_color, sys_label = "#F59E0B", "Warning"
    elif active > 0:
        dot_color, sys_label = "#10B981", "Online"
    else:
        dot_color, sys_label = "#6B7280", "Idle"

    pnl = summary.get("total_pnl", 0)
    pnl_color = "#10B981" if pnl > 0 else "#EF4444" if pnl < 0 else "#9CA3AF"

    bar_html = (
        '<div class="status-bar">'
        f'<span class="status-item">'
        f'<span class="status-dot" style="background:{dot_color}"></span>'
        f'<span class="status-val">{sys_label}</span></span>'
        '<span class="status-divider">|</span>'
        f'<span class="status-item">Mode: '
        f'<span class="status-val">{mode}</span></span>'
        '<span class="status-divider">|</span>'
        f'<span class="status-item">Active Strategies: '
        f'<span class="status-val">{active}</span></span>'
        '<span class="status-divider">|</span>'
        f'<span class="status-item">Margin Used: '
        f'<span class="status-val">{csym}{summary.get("margin_used", 0):,.0f}</span></span>'
        '<span class="status-divider">|</span>'
        f'<span class="status-item">Portfolio PnL: '
        f'<span style="color:{pnl_color};font-weight:700">'
        f'{csym}{pnl:+,.2f}</span></span>'
        '</div>'
    )
    st.markdown(bar_html, unsafe_allow_html=True)

_system_status_bar()

# ── Tabs ─────────────────────────────────────────────────────────────────
tab_guide, tab_market, tab_terminal = st.tabs(["Guide", "Market Viewer", "Trading Terminal"])

# ═══════════════════════════════════════════════════════════════════════════
# TAB: Trading Terminal
# ═══════════════════════════════════════════════════════════════════════════
with tab_terminal:
    if trading_mode == "Simulation":
        st.info("**Simulation Mode** — no real trades will be executed.")
    else:
        st.warning("**Live Trading Mode** — real orders will be executed.")

    col_main, col_right = st.columns([3, 1], gap="medium")

    # ── Right panel — trading controls ───────────────────────────────
    with col_right:
        with st.container(border=True):
            st.markdown('<p class="section-lbl">Position Settings</p>', unsafe_allow_html=True)
            leverage = st.number_input("Leverage", min_value=1, max_value=125, value=10, step=1)

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
                min_value=min_notional_val, value=default_notional,
                step=100.0 if margin_currency == "INR" else 10.0,
                help="Total position value. Margin = Notional / Leverage.",
            )
            if max_notional_val and max_notional_val > min_notional_val:
                notional_kwargs["max_value"] = max_notional_val
            notional_value = st.number_input(**notional_kwargs)

            if instr and preview_price and leverage > 0:
                n_usdt = notional_value
                if margin_currency == "INR" and preview_usdt_inr and preview_usdt_inr > 0:
                    n_usdt = notional_value / preview_usdt_inr
                from bot.exchange_precision import snap_quantity as _base_snap
                est_qty = _base_snap(n_usdt / preview_price, instr["quantity_increment"])
                est_qty = max(est_qty, instr["min_quantity"])
                margin_req_preview = notional_value / leverage
                st.caption(
                    f"Qty ≈ {est_qty:.6f} · Margin {currency_symbol}{margin_req_preview:,.2f}"
                )
            order_type = st.selectbox("Order Type", ["market", "limit"])

        with st.container(border=True):
            st.markdown('<p class="section-lbl">Risk</p>', unsafe_allow_html=True)
            tp_percent = st.number_input("Take Profit %", min_value=0.01, max_value=100.0, value=3.0, step=0.1)
            sl_percent = st.number_input("Stop Loss %", min_value=0.01, max_value=100.0, value=2.0, step=0.1)

        with st.container(border=True):
            st.markdown('<p class="section-lbl">Direction</p>', unsafe_allow_html=True)
            strategy_mode = st.selectbox("Strategy Mode", ["momentum", "reversal"])
            direction = st.selectbox("Direction", ["LONG", "SHORT"])

        with st.container(border=True):
            st.markdown('<p class="section-lbl">Portfolio Risk</p>', unsafe_allow_html=True)
            _margin_default = (
                float(mgr.max_portfolio_margin)
                if st.session_state.connected else 50_000.0
            )
            max_port_margin = st.number_input(
                f"Max Portfolio Margin ({margin_currency})",
                min_value=100.0, max_value=10_000_000.0,
                value=_margin_default, step=1000.0,
                help="Maximum total margin across all active strategies.",
            )
            if st.session_state.connected:
                mgr.max_portfolio_margin = max_port_margin

        start_clicked = st.button("Start Strategy", use_container_width=True, type="primary")

        if st.session_state.connected:
            try:
                active_ids = mgr.get_active_strategy_ids() or []
            except Exception:
                active_ids = []
        else:
            active_ids = []
        if active_ids:
            stop_id = st.selectbox("Stop Strategy", active_ids, key="stop_select")
            stop_clicked = st.button("Stop Selected", use_container_width=True)
        else:
            stop_id = None
            stop_clicked = False

        if st.session_state.connected:
            try:
                all_strats = mgr.get_all_strategies() or []
            except Exception:
                all_strats = []
        else:
            all_strats = []
        finished_ids = [
            s["id"] for s in all_strats
            if s["status"] in ("stopped", "expired", "closed", "error", "waiting")
            and not s.get("is_alive")
        ]
        if finished_ids:
            remove_id = st.selectbox("Remove Strategy", finished_ids, key="remove_select")
            remove_clicked = st.button("Remove Selected", use_container_width=True)
        else:
            remove_id = None
            remove_clicked = False

    # ── Button logic ─────────────────────────────────────────────────
    if start_clicked:
        if not st.session_state.connected:
            st.error(
                "Connect your CoinDCX API key first. "
                "Open **Backend Connection** in the sidebar."
            )
        else:
            required_margin = notional_value / max(leverage, 1)
            allowed, msg = mgr.can_start_strategy(required_margin)
            if not allowed:
                st.error(msg)
            else:
                params = {
                    "api_key": api_key, "api_secret": api_secret,
                    "pair": pair, "dip_percent": dip_percent,
                    "comparison_window_minutes": comparison_window,
                    "check_frequency_seconds": check_frequency,
                    "strategy_expiry_minutes": strategy_expiry,
                    "notional": notional_value, "leverage": leverage,
                    "order_type": order_type,
                    "take_profit_percent": tp_percent,
                    "stop_loss_percent": sl_percent,
                    "direction": direction, "strategy_mode": strategy_mode,
                    "margin_currency": margin_currency,
                    "trading_mode": trading_mode.lower(),
                    "sim_balance": sim_balance,
                }
                safe_params = {k: v for k, v in params.items() if k not in ("api_key", "api_secret")}
                with open(RUNTIME_CONFIG_PATH, "w") as f:
                    json.dump(safe_params, f, indent=2)
                sid = mgr.register_strategy(params)
                mgr.start_strategy(sid)
                st.rerun()

    if stop_clicked and stop_id:
        mgr.stop_strategy(stop_id)
        time.sleep(0.3)
        st.rerun()

    if remove_clicked and remove_id:
        mgr.remove_strategy(remove_id)
        st.rerun()

    # ── Center dashboard — auto-refreshing strategy table ────────────
    @st.fragment(run_every=timedelta(seconds=10))
    def _strategy_dashboard():
        _fb_summary = {
            "active_strategies": 0, "total_pnl": 0.0,
            "margin_used": 0.0, "margin_available": 0.0,
        }
        if not st.session_state.connected:
            st.caption("Connect your CoinDCX API key to load strategies.")
            return
        try:
            strategies = mgr.get_all_strategies() or []
        except Exception:
            strategies = []
        try:
            summary = mgr.get_portfolio_summary() or _fb_summary
        except Exception:
            summary = _fb_summary
        mc = margin_currency
        csym = "₹" if mc == "INR" else "$"
        now = time.time()

        st.markdown(
            '<p class="section-lbl" style="margin-top:0">Portfolio Overview</p>',
            unsafe_allow_html=True,
        )
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            st.metric("Active Strategies", summary["active_strategies"])
        with s2:
            total_pnl = summary["total_pnl"]
            st.metric(
                "Total PnL",
                f"{csym}{total_pnl:+,.2f}",
                delta=f"{total_pnl:+,.2f}" if total_pnl != 0 else None,
            )
        with s3:
            st.metric("Margin Used", f"{csym}{summary['margin_used']:,.2f}")
        with s4:
            st.metric("Available", f"{csym}{summary['margin_available']:,.2f}")

        st.markdown('<hr class="hr">', unsafe_allow_html=True)

        st.markdown(
            '<p class="section-lbl">Active Strategies</p>',
            unsafe_allow_html=True,
        )

        if not strategies:
            st.caption("No strategies yet. Configure and start one from the right panel.")
            return

        rows = []
        for s in strategies:
            status_raw = s.get("status", "waiting")
            s_direction = s.get("direction", "?")
            mode = s.get("strategy_mode", "momentum")
            pnl = s.get("pnl", 0) or 0
            margin_val = s.get("margin", 0) or 0
            notional_val = s.get("notional", 0) or 0
            leverage_val = s.get("leverage", 0)
            created = s.get("created_at", now)
            expiry_min = s.get("strategy_expiry_minutes", 1440)
            expiry_epoch = created + expiry_min * 60
            remaining = expiry_epoch - now

            rows.append({
                "Strategy": _strategy_name(s_direction, mode),
                "Pair": s.get("pair_label", "?"),
                "Direction": s_direction,
                "Mode": mode.title(),
                "Leverage": f"{leverage_val}x",
                "Margin": f"{csym}{margin_val:,.2f}",
                "Notional": f"{csym}{notional_val:,.2f}",
                "Status": _STATUS_LABELS.get(status_raw, status_raw.title()),
                "Entry Price": _fmt_price(s.get("entry_price")),
                "TP Price": _fmt_price(s.get("tp_price"), "—"),
                "SL Price": _fmt_price(s.get("sl_price"), "—"),
                "PnL": _fmt_pnl(pnl, csym),
                "Running Time": _fmt_duration(now - created),
                "Expiry Time": _fmt_duration(remaining) if remaining > 0 else "Expired",
            })

        df = pd.DataFrame(rows)

        def _color_pnl(val: str):
            if "+" in val:
                return "color: #10B981; font-weight: 700"
            if "-" in val:
                return "color: #EF4444; font-weight: 700"
            return "color: #6B7280"

        styled = df.style.map(_color_pnl, subset=["PnL"])

        st.dataframe(styled, width="stretch", hide_index=True)

    with col_main:
        _strategy_dashboard()

    # ── Bottom — full-width event feed ───────────────────────────────
    st.markdown('<hr class="hr">', unsafe_allow_html=True)

    @st.fragment(run_every=timedelta(seconds=10))
    def _event_feed():
        st.markdown(
            '<p class="section-lbl" style="margin-top:4px">Trading Events</p>',
            unsafe_allow_html=True,
        )
        if not st.session_state.connected:
            st.caption("Connect to see trading events.")
            return
        try:
            logs = list(mgr.logs)
        except Exception:
            logs = []
        with st.expander("Event Log", expanded=bool(logs)):
            if logs:
                st.text_area("logs_main", value="\n".join(logs[-100:]), height=240,
                             disabled=True, label_visibility="collapsed")
            else:
                st.caption("No events yet — start a strategy to see activity here.")

    _event_feed()

# ═══════════════════════════════════════════════════════════════════════════
# TAB: Market Viewer
# ═══════════════════════════════════════════════════════════════════════════
with tab_market:

    @st.fragment(run_every=timedelta(seconds=10))
    def _market_viewer():
        # ── Controls row ─────────────────────────────────────────────
        c1, c2, c3 = st.columns(3)
        with c1:
            mv_pair_label = st.selectbox(
                "Token", sorted(PAIR_OPTIONS.keys()), key="mv_token",
            )
        with c2:
            mv_interval = st.selectbox(
                "Chart Interval", list(_MV_INTERVAL_LABELS.keys()),
                format_func=lambda x: _MV_INTERVAL_LABELS[x],
                index=2, key="mv_interval",
            )
        with c3:
            mv_window = st.number_input(
                "Strategy Comparison Window (hours)",
                min_value=1, max_value=72, value=1, key="mv_window",
            )

        instrument = PAIR_OPTIONS[mv_pair_label]
        now_ts = int(time.time())

        # ── Fetch live trades (used for LTP + recent trades table) ───
        trades: list = []
        ltp: Optional[float] = None
        try:
            trades = _mv_fetch_trades(instrument)
            if trades:
                ltp = float(trades[-1]["price"])
        except Exception:
            pass

        # ── Price change over strategy window ────────────────────────
        price_change: Optional[float] = None
        try:
            w_from = now_ts - mv_window * 3600
            w_candles = _mv_fetch_candles(instrument, "60", w_from, now_ts)
            if w_candles and ltp is not None:
                past_price = float(w_candles[0]["close"])
                if past_price > 0:
                    price_change = ((ltp - past_price) / past_price) * 100
        except Exception:
            pass

        # ── Metrics row: LTP + Price Change ──────────────────────────
        m1, m2 = st.columns(2)
        with m1:
            st.metric("LTP", f"{ltp:,.4f}" if ltp is not None else "—")
        with m2:
            if price_change is not None:
                st.metric(
                    f"Price Change ({mv_window}h)",
                    f"{price_change:+.2f}%",
                    delta=f"{price_change:+.2f}%",
                )
            else:
                st.metric(f"Price Change ({mv_window}h)", "—")

        st.markdown('<hr class="hr">', unsafe_allow_html=True)

        # ── Candlestick chart ────────────────────────────────────────
        try:
            lookback = 200 * _MV_INTERVAL_SECONDS[mv_interval]
            chart_from = now_ts - lookback
            candles = _mv_fetch_candles(instrument, mv_interval, chart_from, now_ts)

            if candles:
                times = [datetime.utcfromtimestamp(c["time"] / 1000) for c in candles]
                fig = go.Figure(data=[
                    go.Candlestick(
                        x=times,
                        open=[float(c["open"]) for c in candles],
                        high=[float(c["high"]) for c in candles],
                        low=[float(c["low"]) for c in candles],
                        close=[float(c["close"]) for c in candles],
                        increasing_line_color="#10B981",
                        decreasing_line_color="#EF4444",
                        increasing_fillcolor="rgba(16,185,129,0.35)",
                        decreasing_fillcolor="rgba(239,68,68,0.35)",
                    )
                ])
                fig.update_layout(
                    template="plotly_dark",
                    paper_bgcolor="#111827",
                    plot_bgcolor="#0B0F19",
                    xaxis=dict(
                        gridcolor="rgba(255,255,255,0.04)",
                        rangeslider_visible=False,
                    ),
                    yaxis=dict(gridcolor="rgba(255,255,255,0.04)"),
                    margin=dict(l=0, r=0, t=30, b=0),
                    height=420,
                )
                st.plotly_chart(fig, use_container_width=True, key="mv_chart")
            else:
                st.caption("No chart data available for this instrument.")
        except Exception:
            st.caption("Market data temporarily unavailable.")

        st.markdown('<hr class="hr">', unsafe_allow_html=True)

        # ── Orderbook (top 5 bids + asks) ────────────────────────────
        try:
            ob = _mv_fetch_orderbook(instrument)
            bids_raw = ob.get("bids", [])[:5]
            asks_raw = ob.get("asks", [])[:5]

            ob1, ob2 = st.columns(2)
            with ob1:
                st.markdown(
                    '<p class="section-lbl">Buy Orders (Bids)</p>',
                    unsafe_allow_html=True,
                )
                bid_rows = _parse_ob_entries(bids_raw)
                if bid_rows:
                    st.dataframe(
                        pd.DataFrame(bid_rows),
                        width="stretch", hide_index=True,
                    )
                else:
                    st.caption("No bids available.")
            with ob2:
                st.markdown(
                    '<p class="section-lbl">Sell Orders (Asks)</p>',
                    unsafe_allow_html=True,
                )
                ask_rows = _parse_ob_entries(asks_raw)
                if ask_rows:
                    st.dataframe(
                        pd.DataFrame(ask_rows),
                        width="stretch", hide_index=True,
                    )
                else:
                    st.caption("No asks available.")
        except Exception:
            st.caption("Orderbook data temporarily unavailable.")

        st.markdown('<hr class="hr">', unsafe_allow_html=True)

        # ── Recent trades ────────────────────────────────────────────
        try:
            if trades:
                st.markdown(
                    '<p class="section-lbl">Recent Trades</p>',
                    unsafe_allow_html=True,
                )
                recent = trades[-10:][::-1]
                trade_rows = []
                for t in recent:
                    ts_raw = t.get("timestamp", t.get("T", t.get("time", "")))
                    if isinstance(ts_raw, (int, float)):
                        if ts_raw > 1e12:
                            ts_raw = ts_raw / 1000
                        ts_str = datetime.utcfromtimestamp(ts_raw).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                    else:
                        ts_str = str(ts_raw)

                    trade_rows.append({
                        "Price": float(t.get("price", 0)),
                        "Quantity": float(t.get("quantity", t.get("q", 0))),
                        "Time": ts_str,
                    })
                st.dataframe(
                    pd.DataFrame(trade_rows),
                    width="stretch", hide_index=True,
                )
            else:
                st.caption("No recent trades available.")
        except Exception:
            st.caption("Recent trades temporarily unavailable.")

    _market_viewer()

# ═══════════════════════════════════════════════════════════════════════════
# TAB: Guide
# ═══════════════════════════════════════════════════════════════════════════
with tab_guide:

    st.subheader("Automated Futures Strategy Bot")
    st.markdown(
        "This bot allows users to create automated futures trading strategies on "
        "CoinDCX. It continuously monitors market prices and executes trades "
        "automatically when strategy conditions are met.\n\n"
        "You can run **multiple strategies simultaneously** while portfolio "
        "guardrails prevent excessive margin usage."
    )

    st.markdown("---")

    # ── How Strategies Work ──────────────────────────────────────────
    st.subheader("How Strategies Work")
    st.markdown(
        "**Dip / Rise Strategy** — Opens a position when the price moves by a "
        "specified percentage within a configured time window.\n\n"
        "**Momentum Mode** — Follows the direction of the price movement. "
        "A LONG triggers when price rises by the dip %; a SHORT triggers when "
        "price falls.\n\n"
        "**Reversal Mode** — Trades against the short-term movement. "
        "A LONG triggers when price *drops* by the dip %; a SHORT triggers when "
        "price *rises*."
    )

    st.markdown("---")

    # ── How To Run A Strategy ────────────────────────────────────────
    st.subheader("How To Run A Strategy")
    st.markdown(
        "1. Select a **trading pair** (BTC, ETH, SOL) in the sidebar.\n"
        "2. Choose **leverage** in the right panel.\n"
        "3. Set the **position notional** (total position value).\n"
        "4. Configure **dip %** and **comparison window** in the sidebar.\n"
        "5. Set **Take Profit %** and **Stop Loss %**.\n"
        "6. Click **Start Strategy**.\n\n"
        "The strategy will begin scanning the market at the configured frequency. "
        "When the entry condition is met, an order is placed automatically."
    )

    st.markdown("---")

    # ── Live System Status ───────────────────────────────────────────
    st.subheader("Live System Status")

    @st.fragment(run_every=timedelta(seconds=10))
    def _guide_live_status():
        if not st.session_state.connected:
            st.caption("Connect your CoinDCX API key to see live system status.")
            return
        _fb = {
            "active_strategies": 0, "total_pnl": 0.0,
            "margin_used": 0.0, "margin_available": 0.0,
        }
        try:
            summary = mgr.get_portfolio_summary() or _fb
        except Exception:
            summary = _fb
        csym = "₹" if margin_currency == "INR" else "$"
        g1, g2, g3, g4 = st.columns(4)
        with g1:
            st.metric("Active Strategies", summary["active_strategies"])
        with g2:
            st.metric("Margin Used", f"{csym}{summary['margin_used']:,.2f}")
        with g3:
            st.metric("Available", f"{csym}{summary['margin_available']:,.2f}")
        with g4:
            st.metric("Total PnL", f"{csym}{summary['total_pnl']:+,.2f}")
        st.markdown(f"**Trading Mode:** {st.session_state.trading_mode}")

    _guide_live_status()

    st.markdown("---")

    # ── Strategy Lifecycle ───────────────────────────────────────────
    st.subheader("Strategy Lifecycle")
    st.markdown(
        "| Status | Meaning |\n"
        "|---|---|\n"
        "| **Scanning** | Waiting for the entry signal |\n"
        "| **Order Placed** | Entry order submitted to the exchange |\n"
        "| **Open** | Position is currently active |\n"
        "| **Closed** | Position exited via TP or SL |\n"
        "| **Expired** | Strategy timed out before finding an entry |\n"
        "| **Error** | Strategy encountered an issue |"
    )

    st.markdown("---")

    # ── Risk Guardrails ──────────────────────────────────────────────
    st.subheader("Risk Guardrails")
    st.markdown(
        "The system enforces a **maximum portfolio margin** limit. This prevents "
        "too many strategies from opening positions simultaneously and limits "
        "total capital at risk.\n\n"
        "Before a new strategy can start, the bot checks that the required margin "
        "fits within the remaining portfolio budget."
    )

    st.markdown("---")

    # ── Where To Look ────────────────────────────────────────────────
    st.subheader("Where To Look In The Dashboard")
    st.markdown(
        "| Area | What It Shows |\n"
        "|---|---|\n"
        "| **Strategy Table** | All running strategies with status, PnL, and prices |\n"
        "| **Portfolio Summary** | Aggregate margin usage and PnL across strategies |\n"
        "| **Sidebar Logs** | System events, API calls, and debugging information |\n"
        "| **Event Log** | Timestamped trading activity feed |"
    )
