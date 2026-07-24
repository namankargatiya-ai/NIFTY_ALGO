"""
Central configuration for the Nifty Algo Trader.

Credentials are read from (in order of preference):
  1. Streamlit Cloud secrets (st.secrets) — used when deployed, so the
     token/keys never need to live in a file on the server at all.
  2. Environment variables, loaded from a local `.env` file via
     python-dotenv — used for local runs (python app.py, or streamlit run
     locally with no secrets.toml).
Never hardcode secrets here. Copy `.env.example` to `.env` and fill in your
own Upstox app credentials before running `mode="live"`.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_secret(key, default=""):
    try:
        import streamlit as st
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass  # not running under Streamlit, or no secrets.toml configured
    return os.getenv(key, default)


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------
# "paper"  -> orders/paper_trader.py simulates fills (safe default, no broker
#             credentials required for order placement; still uses the
#             broker's real data endpoints if UPSTOX_ACCESS_TOKEN is set,
#             otherwise falls back to utils/synthetic_data.py)
# "live"   -> orders/live_trader.py places REAL orders via broker/upstox_api.py
#             Only switch this once you have tested "paper" thoroughly.
TRADING_MODE = _get_secret("TRADING_MODE", "paper")

# ---------------------------------------------------------------------------
# Upstox credentials (required for live data / live trading)
# ---------------------------------------------------------------------------
UPSTOX_API_KEY = _get_secret("UPSTOX_API_KEY", "")
UPSTOX_API_SECRET = _get_secret("UPSTOX_API_SECRET", "")
UPSTOX_REDIRECT_URI = _get_secret("UPSTOX_REDIRECT_URI", "https://localhost:3000/callback")
UPSTOX_ACCESS_TOKEN = _get_secret("UPSTOX_ACCESS_TOKEN", "")  # generated daily via broker/upstox_api.py login flow
UPSTOX_API_VERSION = "2.0"

# ---------------------------------------------------------------------------
# Instrument
# ---------------------------------------------------------------------------
UNDERLYING_INSTRUMENT_KEY = "NSE_INDEX|Nifty 50"   # Upstox instrument key for spot NIFTY
LOT_SIZE = 65                                       # per Upstox's current NIFTY contract data (verified 2026-07-19)
STRIKE_STEP = 50

# ---------------------------------------------------------------------------
# Session / timeframe rules
# ---------------------------------------------------------------------------
SESSION1_START = "09:15"
SESSION1_END = "10:30"
MARKET_CLOSE = "15:30"
SESSION1_CANDLE_MINUTES = 1
SESSION2_CANDLE_MINUTES = 3
TREND_FILTER_TIMEFRAME_MINUTES = 15
EMA_PERIOD = 20

# ---------------------------------------------------------------------------
# Risk management
# ---------------------------------------------------------------------------
MIN_SL_POINTS = 14.0            # fixed stop loss, in option premium points — never changes, no buffer
MIN_TARGET_POINTS = 15.0        # fixed profit target, in option premium points — never changes, no trailing
MAX_ACCEPTABLE_RISK_POINTS = 60.0  # technical SL beyond this -> skip the trade
POSITION_SIZE_LOTS = 1
ALLOW_PYRAMIDING = False

# ---------------------------------------------------------------------------
# Option pricing (used by paper_trader when no live option-chain LTP exists)
# ---------------------------------------------------------------------------
DEFAULT_IV = 0.13
RISK_FREE_RATE = 0.07

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DATA_DIR = os.path.join(BASE_DIR, "data")
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
LIVE_STATE_PATH = os.path.join(DATA_DIR, "live_state.json")  # shared between app.py and dashboard/dashboard.py

# ---------------------------------------------------------------------------
# Brokerage estimate (used only in the EOD report)
# ---------------------------------------------------------------------------
ESTIMATED_CHARGES_PER_TRADE = 40.0  # placeholder flat estimate; replace with your broker's real charges
