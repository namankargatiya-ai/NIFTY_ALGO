"""
Simplified Black-Scholes option pricer.

This is a fallback/demo pricing model for paper trading when a live
option-chain LTP isn't available. When UPSTOX_ACCESS_TOKEN is configured,
orders/paper_trader.py prefers real option-chain LTPs from
broker/upstox_api.py and only falls back to this module if that call fails.
"""
import numpy as np
from scipy.stats import norm
import config


def bs_price_delta(spot, strike, T_years, iv=None, option_type="CALL", r=None):
    iv = iv if iv is not None else config.DEFAULT_IV
    r = r if r is not None else config.RISK_FREE_RATE
    T_years = max(T_years, 1e-6)

    d1 = (np.log(spot / strike) + (r + 0.5 * iv ** 2) * T_years) / (iv * np.sqrt(T_years))
    d2 = d1 - iv * np.sqrt(T_years)

    if option_type == "CALL":
        price = spot * norm.cdf(d1) - strike * np.exp(-r * T_years) * norm.cdf(d2)
        delta = norm.cdf(d1)
    else:
        price = strike * np.exp(-r * T_years) * norm.cdf(-d2) - spot * norm.cdf(-d1)
        delta = norm.cdf(d1) - 1

    return max(price, 0.05), delta


def time_to_expiry_years(current_dt, expiry_dt):
    seconds_left = (expiry_dt - current_dt).total_seconds()
    return max(seconds_left, 0) / (365.0 * 24 * 3600)


def nearest_itm_strikes(spot, strike_step=None):
    """Nearest ITM call & put strikes (one step ITM from ATM)."""
    step = strike_step or config.STRIKE_STEP
    atm = round(spot / step) * step
    itm_call_strike = atm - step
    itm_put_strike = atm + step
    return itm_call_strike, itm_put_strike
