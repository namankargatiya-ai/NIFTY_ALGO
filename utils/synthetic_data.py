"""
Synthetic NIFTY intraday data generator.

Used only as a fallback for local testing when no Upstox connection is
configured (no UPSTOX_ACCESS_TOKEN). The moment real credentials are set,
orders/paper_trader.py and app.py pull real historical/intraday candles from
broker/upstox_api.py instead — nothing else in the pipeline changes, since
both paths return the same OHLC DataFrame shape (columns: datetime, open,
high, low, close).
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import config


def _session_minutes(date, start_str=config.SESSION1_START, end_str=config.MARKET_CLOSE):
    start = datetime.combine(date, datetime.strptime(start_str, "%H:%M").time())
    end = datetime.combine(date, datetime.strptime(end_str, "%H:%M").time())
    n = int((end - start).total_seconds() // 60)
    return [start + timedelta(minutes=i) for i in range(n)]


def _gen_day_1min(date, open_price, seed, regime="mixed"):
    rng = np.random.default_rng(seed)
    times = _session_minutes(date)
    n = len(times)

    drift = {"bullish": 0.35, "bearish": -0.35}.get(regime, 0.0)
    base_vol = open_price * 0.00045

    prices = [open_price]
    for i in range(1, n):
        vol_state = base_vol * (0.35 if rng.random() < 0.06 else rng.uniform(0.7, 1.3))
        prices.append(prices[-1] + rng.normal(drift, vol_state))

    prices = np.array(prices)
    bars = []
    for i, t in enumerate(times):
        o = prices[i - 1] if i > 0 else prices[0]
        c = prices[i]
        hi = max(o, c) + abs(rng.normal(0, base_vol * 0.25))
        lo = min(o, c) - abs(rng.normal(0, base_vol * 0.25))
        bars.append({"datetime": t, "open": o, "high": hi, "low": lo, "close": c})
    return pd.DataFrame(bars)


def gen_history_and_today(today, base_price=24800.0, n_history_days=15, seed=42):
    rng = np.random.default_rng(seed)
    regimes = ["bullish", "bearish", "mixed"]
    price = base_price
    history_frames = []

    day = today - timedelta(days=n_history_days)
    d_seed = seed
    while day < today:
        if day.weekday() < 5:
            regime = regimes[rng.integers(0, 3)]
            day_df = _gen_day_1min(day, price, d_seed, regime=regime)
            history_frames.append(day_df)
            price = day_df["close"].iloc[-1]
            d_seed += 1
        day += timedelta(days=1)

    history_1min = pd.concat(history_frames, ignore_index=True).set_index("datetime")
    history_15min = history_1min.resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()

    today_regime = regimes[rng.integers(0, 3)]
    today_1min = _gen_day_1min(today, price, d_seed + 1, regime=today_regime)

    return history_15min.reset_index(), today_1min, today_regime
