"""
Streamlit dashboard for the Nifty Algo Trader.

Run alongside app.py to watch it live:
    python app.py --delay 0.05          # terminal 1 (use a small delay so you can watch it update)
    streamlit run dashboard/dashboard.py  # terminal 2

Or just run `python app.py` first and then this dashboard to inspect the
completed day's results — it reads whatever is currently in data/live_state.json.
"""
import json
import os
import sys
import time

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

st.set_page_config(page_title="Nifty Algo Trader", layout="wide", page_icon="📈")

# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------
st.sidebar.title("Nifty Algo Trader")
st.sidebar.caption("Paper-trading dashboard")
auto_refresh = st.sidebar.checkbox("Auto-refresh", value=True)
refresh_secs = st.sidebar.slider("Refresh interval (s)", 1, 10, 2)
if st.sidebar.button("Refresh now"):
    st.rerun()

# ---------------------------------------------------------------------------
# Load shared state
# ---------------------------------------------------------------------------
if not os.path.exists(config.LIVE_STATE_PATH):
    st.warning(
        f"No state file found yet at `{config.LIVE_STATE_PATH}`.\n\n"
        "Run `python app.py` first (in paper mode this needs no credentials)."
    )
    st.stop()

with open(config.LIVE_STATE_PATH) as f:
    state = json.load(f)

meta = state.get("meta", {})
bars = state.get("bars", [])
trades = state.get("trades", [])

if not bars:
    st.info("Waiting for the first bar...")
    st.stop()

last = bars[-1]

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.markdown(
    f"##  NIFTY Intraday Options — {meta.get('trade_date', '')}  "
    f"·  data source: `{meta.get('data_source', 'unknown')}`  ·  expiry: {meta.get('expiry', '')[:10]}"
)
if meta.get("data_source") == "synthetic":
    st.caption("⚠️ Synthetic data — no live broker/market-data feed connected.")

cols = st.columns(6)
cols[0].metric("Live NIFTY", f"{last['spot']:.2f}")
cols[1].metric("Session", last["session"])
cols[2].metric("Timeframe", last["timeframe"])
trend = last.get("trend", "N/A")
cols[3].metric("HTF Trend", trend if trend != "N/A" else "—")
cols[4].metric("EMA(20)", f"{last['ema20']:.2f}" if last.get("ema20") is not None else "n/a")
cols[5].metric("Status", last["status"])

st.divider()

# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------
df = pd.DataFrame(bars)
df["datetime"] = pd.to_datetime(df["datetime"])
chart_df = df.set_index("datetime")[["spot"]].rename(columns={"spot": "NIFTY Spot"})
if df["ema20"].notna().any():
    chart_df["EMA(20)"] = df.set_index("datetime")["ema20"]
st.line_chart(chart_df, height=340)

# ---------------------------------------------------------------------------
# Active trade + session stats
# ---------------------------------------------------------------------------
left, right = st.columns([1, 1])

with left:
    st.subheader("Active Trade")
    if last.get("active_trade_type"):
        pnl = last.get("active_pnl_rs")
        pnl_color = "normal" if (pnl or 0) >= 0 else "inverse"
        c1, c2, c3 = st.columns(3)
        c1.metric("Type", last["active_trade_type"])
        c1.metric("Symbol", last.get("active_symbol", "—"))
        c2.metric("Entry", last["active_entry"])
        c2.metric("Current", last.get("active_premium", last["active_entry"]))
        c3.metric("Stop Loss", last["active_sl"])
        c3.metric("Target", last["active_target"])
        pnl_pts = last.get("active_pnl_pts")
        st.metric("Live P/L", f"₹{pnl:,.2f}" if pnl is not None else "—",
                   delta=f"{pnl_pts:.2f} pts" if pnl_pts is not None else "—")
    else:
        st.info("No active position — scanning for setup")

with right:
    st.subheader("Session Stats")
    total = len(trades)
    wins = [t for t in trades if (t.get("pnl_rupees") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl_rupees") or 0) <= 0]
    net = sum(t.get("pnl_rupees") or 0 for t in trades)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trades", total)
    c2.metric("Wins", len(wins))
    c3.metric("Losses", len(losses))
    c4.metric("Win Rate", f"{100*len(wins)/total:.0f}%" if total else "0%")
    st.metric("Net P/L", f"₹{net:,.2f}")

st.divider()

# ---------------------------------------------------------------------------
# Trade log
# ---------------------------------------------------------------------------
st.subheader("Trade Log")
if trades:
    tdf = pd.DataFrame(trades)[
        ["date", "entry_time", "exit_time", "trade_type", "strike", "option_symbol",
         "entry_premium", "exit_premium", "stop_loss", "target",
         "pnl_points", "pnl_rupees", "exit_reason", "duration"]
    ]
    st.dataframe(tdf, use_container_width=True, hide_index=True)
else:
    st.caption("No trades closed yet")

# ---------------------------------------------------------------------------
# EOD report
# ---------------------------------------------------------------------------
if meta.get("eod_stats"):
    st.divider()
    st.subheader("📋 End-of-Day Performance Report")
    stats = meta["eod_stats"]
    eod_cols = st.columns(4)
    for i, (k, v) in enumerate(stats.items()):
        eod_cols[i % 4].metric(k, v)

# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------
if auto_refresh:
    time.sleep(refresh_secs)
    st.rerun()
