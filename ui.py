"""
Streamlit web UI for the CoinDCX Futures multi-strategy trading terminal.

Run with:
    streamlit run ui.py
"""

import json
import os
import time
from datetime import timedelta
from typing import Optional

import pandas as pd
import streamlit as st

from bot.strategy_manager import StrategyManager

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
    st.session_state.strategy_manager = StrategyManager()
if "instrument_rules" not in st.session_state:
    st.session_state.instrument_rules = None
if "preview_price" not in st.session_state:
    st.session_state.preview_price = None
if "preview_usdt_inr" not in st.session_state:
    st.session_state.preview_usdt_inr = None
if "trading_mode" not in st.session_state:
    st.session_state.trading_mode = "Simulation"

mgr: StrategyManager = st.session_state.strategy_manager


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

    st.markdown('<p class="section-lbl">Connection</p>', unsafe_allow_html=True)
    api_key = st.text_input("API Key", type="password", value=os.getenv("COINDCX_API_KEY", ""))
    api_secret = st.text_input("API Secret", type="password", value=os.getenv("COINDCX_API_SECRET", ""))
    if trading_mode == "Simulation":
        st.caption("Optional in Simulation mode")
    else:
        st.caption("Required for Live trading")
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
        logs = list(mgr.logs)
        if logs:
            st.text_area("log_output", value="\n".join(logs[-150:]), height=300,
                         disabled=True, label_visibility="collapsed")
        else:
            st.caption("No events yet.")


# ── Fetch instrument data ───────────────────────────────────────────────
_fetch_instrument_preview(pair, margin_currency)

instr = st.session_state.instrument_rules
preview_price = st.session_state.preview_price
preview_usdt_inr = st.session_state.preview_usdt_inr

# ── Mode banner ─────────────────────────────────────────────────────────
if trading_mode == "Simulation":
    st.info("**Simulation Mode** — no real trades will be executed.")
else:
    st.warning("**Live Trading Mode** — real orders will be executed.")

# ── Main layout ─────────────────────────────────────────────────────────
col_main, col_right = st.columns([3, 1], gap="medium")

# ═══════════════════════════════════════════════════════════════════════════
# RIGHT PANEL — trading controls
# ═══════════════════════════════════════════════════════════════════════════
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
        max_port_margin = st.number_input(
            f"Max Portfolio Margin ({margin_currency})",
            min_value=100.0, max_value=10_000_000.0,
            value=float(mgr.max_portfolio_margin), step=1000.0,
            help="Maximum total margin across all active strategies.",
        )
        mgr.max_portfolio_margin = max_port_margin

    start_clicked = st.button("Start Strategy", use_container_width=True, type="primary")

    active_ids = mgr.get_active_strategy_ids()
    if active_ids:
        stop_id = st.selectbox("Stop Strategy", active_ids, key="stop_select")
        stop_clicked = st.button("Stop Selected", use_container_width=True)
    else:
        stop_id = None
        stop_clicked = False

    all_strats = mgr.get_all_strategies()
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


# ═══════════════════════════════════════════════════════════════════════════
# Button logic
# ═══════════════════════════════════════════════════════════════════════════
if start_clicked:
    if trading_mode == "Live" and (not api_key or not api_secret):
        st.error("API Key and Secret are required for Live trading.")
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


# ═══════════════════════════════════════════════════════════════════════════
# CENTER DASHBOARD — auto-refreshing strategy table
# ═══════════════════════════════════════════════════════════════════════════

@st.fragment(run_every=timedelta(seconds=5))
def _strategy_dashboard():
    strategies = mgr.get_all_strategies()
    summary = mgr.get_portfolio_summary()
    mc = margin_currency
    csym = "₹" if mc == "INR" else "$"
    now = time.time()

    # ── Portfolio summary metrics ──────────────────────────────────────
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

    # ── Strategy table ────────────────────────────────────────────────
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
        direction = s.get("direction", "?")
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
            "Strategy": _strategy_name(direction, mode),
            "Pair": s.get("pair_label", "?"),
            "Direction": direction,
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

    st.dataframe(styled, use_container_width=True, hide_index=True)


with col_main:
    _strategy_dashboard()


# ═══════════════════════════════════════════════════════════════════════════
# BOTTOM — full-width event feed
# ═══════════════════════════════════════════════════════════════════════════
st.markdown('<hr class="hr">', unsafe_allow_html=True)


@st.fragment(run_every=timedelta(seconds=5))
def _event_feed():
    logs = list(mgr.logs)
    st.markdown(
        '<p class="section-lbl" style="margin-top:4px">Trading Events</p>',
        unsafe_allow_html=True,
    )
    with st.expander("Event Log", expanded=bool(logs)):
        if logs:
            st.text_area("logs_main", value="\n".join(logs[-100:]), height=240,
                         disabled=True, label_visibility="collapsed")
        else:
            st.caption("No events yet — start a strategy to see activity here.")


_event_feed()
