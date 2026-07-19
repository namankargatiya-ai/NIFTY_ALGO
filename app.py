"""
Main entrypoint.

Usage:
    python app.py                      # paper trading, synthetic data (no credentials needed)
    python app.py --date 2026-07-20     # simulate/replay a specific date
    python app.py --delay 0.05          # seconds between bars, so dashboard/dashboard.py
                                         # (running in parallel) can be watched updating live

If config.UPSTOX_ACCESS_TOKEN is set, this attempts to pull real historical/
intraday candles from broker/upstox_api.py; if that call fails for any
reason (no network, expired token, market closed with no data yet, etc.) it
falls back to the synthetic generator so the pipeline always runs end-to-end.

config.TRADING_MODE controls whether orders/paper_trader.py (default, safe)
or orders/live_trader.py (real orders — requires confirm_live=True) executes
the signals produced by strategy/entry_logic.py.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, date as date_cls

import config
from strategy.entry_logic import EntryEngine
from orders.paper_trader import PaperTrader
from reports.trade_logger import write_csv, write_xlsx, trade_to_row
from reports.performance import compute_eod_stats


def resample(df, rule):
    d = df.set_index("datetime")
    out = d.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return out.reset_index()


def _candles_to_df(candles):
    import pandas as pd
    # Upstox timestamps are tz-aware (+05:30); strip to naive so they compare
    # cleanly against the rest of the app's naive datetimes (expiry_dt,
    # session boundaries) — mixing the two raises TypeError on subtraction.
    return pd.DataFrame(
        [{"datetime": datetime.fromisoformat(c[0]).replace(tzinfo=None), "open": c[1], "high": c[2],
          "low": c[3], "close": c[4]} for c in reversed(candles)]
    )


def _latest_ltp(broker):
    """Real last-traded-price from Upstox's live quote endpoint. This can
    differ slightly from the final raw 1-min historical candle's close (NSE
    indices' official last price isn't always identical to the last printed
    intraday tick), so it's used to correct the most recent bar."""
    try:
        quote = broker.get_ltp([config.UNDERLYING_INSTRUMENT_KEY])
        if not quote:
            return None
        row = next(iter(quote.values()))
        last_price = row.get("last_price") if isinstance(row, dict) else getattr(row, "last_price", None)
        return float(last_price) if last_price is not None else None
    except Exception:
        return None


def _patch_last_bar_with_ltp(df, broker):
    """Overwrite the most recent bar's close (and widen high/low if needed)
    with the authoritative live last-traded-price, so the dashboard's
    displayed spot price matches what Upstox/NSE actually report as last."""
    if df is None or not len(df):
        return df
    last_price = _latest_ltp(broker)
    if last_price is None:
        return df
    last_idx = df.index[-1]
    df.loc[last_idx, "close"] = last_price
    df.loc[last_idx, "high"] = max(df.loc[last_idx, "high"], last_price)
    df.loc[last_idx, "low"] = min(df.loc[last_idx, "low"], last_price)
    return df


def load_day_data(trade_date):
    """Returns (history_15min, today_1min, data_source, session_date).

    Prefers real Upstox data. If the market is live today, uses today's
    intraday candles directly. If not (weekend/holiday/pre-open, so
    get_intraday_candles has nothing yet), treats the most recent completed
    trading session as "latest" instead — Upstox's historical endpoint
    already returns data ending at the last real session when `to_date`
    lands on a non-trading day, so no manual holiday calendar is needed.
    Falls back to the synthetic generator only if no live Upstox data is
    reachable at all (no token, no network, etc).
    """
    if config.UPSTOX_ACCESS_TOKEN:
        try:
            from broker.upstox_api import UpstoxBroker
            broker = UpstoxBroker()

            live_candles = broker.get_intraday_candles(config.UNDERLYING_INSTRUMENT_KEY, "minutes", 1)
            session_date = trade_date

            if not live_candles:
                hist_1min = broker.get_historical_candles(
                    config.UNDERLYING_INSTRUMENT_KEY, "minutes", 1, trade_date.isoformat()
                )
                if not hist_1min:
                    raise RuntimeError("no historical candles returned")
                session_date = max(datetime.fromisoformat(c[0]).date() for c in hist_1min)
                live_candles = [c for c in hist_1min if datetime.fromisoformat(c[0]).date() == session_date]

            today_1min = _candles_to_df(live_candles)
            today_1min = _patch_last_bar_with_ltp(today_1min, broker)

            hist_candles = broker.get_historical_candles(
                config.UNDERLYING_INSTRUMENT_KEY, "minutes", 15,
                (session_date - timedelta(days=1)).isoformat()
            )
            history_15min = _candles_to_df(hist_candles) if hist_candles else None

            if history_15min is not None and len(today_1min):
                if session_date == trade_date:
                    print("[app] using live Upstox intraday candles")
                else:
                    print(f"[app] market closed for {trade_date} — using latest available "
                          f"session ({session_date}) as 'today'")
                return history_15min, today_1min, "live", session_date
        except Exception as e:
            print(f"[app] live data fetch failed ({e}); falling back to synthetic data")

    from utils.synthetic_data import gen_history_and_today
    history_15min, today_1min, regime = gen_history_and_today(trade_date)
    print(f"[app] using synthetic data (regime: {regime})")
    return history_15min, today_1min, "synthetic", trade_date


def _next_thursday_expiry(trade_date):
    """Fallback only — used when there's no broker to ask for the real
    expiry (e.g. synthetic data mode). NSE has changed the NIFTY weekly
    expiry weekday before (Thursday -> Tuesday), so this can be wrong;
    resolve_expiry() below prefers real Upstox contract data whenever possible."""
    days_ahead = (3 - trade_date.weekday()) % 7  # Thursday = 3
    days_ahead = days_ahead if days_ahead != 0 else 7
    expiry_date = trade_date + timedelta(days=days_ahead)
    return datetime.combine(expiry_date, datetime.strptime(config.MARKET_CLOSE, "%H:%M").time())


def resolve_expiry(trade_date, broker):
    """Nearest real weekly expiry >= trade_date, from Upstox's actual
    contract listings. Falls back to the static Thursday assumption only
    if no broker/live data is available."""
    if broker is not None:
        try:
            contracts = broker.get_option_contracts(config.UNDERLYING_INSTRUMENT_KEY)
            expiries = sorted({c.expiry.date() for c in (contracts or [])})
            upcoming = [e for e in expiries if e >= trade_date]
            if upcoming:
                return datetime.combine(upcoming[0], datetime.strptime(config.MARKET_CLOSE, "%H:%M").time())
        except Exception as e:
            print(f"[app] expiry lookup failed ({e}); falling back to Thursday assumption")
    return _next_thursday_expiry(trade_date)


def write_live_state(bars, trades_open_rows, meta):
    import os
    os.makedirs(config.DATA_DIR, exist_ok=True)
    state = {"meta": meta, "bars": bars, "trades": trades_open_rows}
    with open(config.LIVE_STATE_PATH, "w") as f:
        json.dump(state, f, default=str)
    return state


def build_state(trade_date=None, delay=0.0, write_files=True):
    """Runs the full pipeline and returns the live-state dict directly —
    {"meta", "bars", "trades"} — the same shape as data/live_state.json.

    write_files=True (the default, used by the CLI via run()) also writes
    the CSV/XLSX/JSON files to disk as before. write_files=False skips all
    file I/O and just returns the dict in memory — used by dashboard.py so
    it can call this directly instead of reading a file that only exists
    when app.py has been run locally (e.g. when deployed, where there's no
    such file at all).
    """
    requested_date = trade_date or datetime.now().date()
    history_15min, today_1min, data_source, trade_date = load_day_data(requested_date)

    broker = None
    if config.UPSTOX_ACCESS_TOKEN and data_source == "live":
        from broker.upstox_api import UpstoxBroker
        broker = UpstoxBroker()

    # Whenever the resolved session isn't the actual real-world today, the
    # live-only option chain has nothing for it (it's a snapshot of right
    # now) — option premiums must come from real historical candles instead.
    # This covers both the automatic weekend/holiday fallback AND an
    # explicitly requested past date (e.g. --date 2026-07-08 for backtesting).
    replay_date = trade_date if (data_source == "live" and trade_date != datetime.now().date()) else None

    expiry_dt = resolve_expiry(trade_date, broker)

    if config.TRADING_MODE == "live":
        from orders.live_trader import LiveTrader
        trader = LiveTrader(broker, confirm_live=True)
        print("[app] *** LIVE TRADING MODE — real orders will be placed ***")
    else:
        trader = PaperTrader()
        print("[app] paper trading mode (no real orders)")

    seed_ema = history_15min["close"].ewm(span=config.EMA_PERIOD, adjust=False).mean().iloc[-1]
    engine = EntryEngine(expiry_dt=expiry_dt, broker=broker, seed_ema_value=seed_ema, replay_date=replay_date)

    session1_end = datetime.strptime(config.SESSION1_END, "%H:%M").time()
    session1 = today_1min[today_1min["datetime"].dt.time < session1_end].reset_index(drop=True)
    session2_1min = today_1min[today_1min["datetime"].dt.time >= session1_end].reset_index(drop=True)
    session2 = resample(session2_1min, f"{config.SESSION2_CANDLE_MINUTES}min") if len(session2_1min) else session2_1min

    bars_log = []

    def current_meta(eod_stats=None):
        meta = {"trade_date": str(trade_date), "data_source": data_source, "expiry": expiry_dt.isoformat()}
        if eod_stats is not None:
            meta["eod_stats"] = eod_stats
        return meta

    def log_bar(dt, spot, session, tf, trend, ema_val, status, live_premium=None):
        t = trader.active_trade
        pnl_pts = round(live_premium - t.entry_premium, 2) if (t and live_premium is not None) else None
        bars_log.append({
            "datetime": dt.isoformat(), "spot": round(spot, 2), "session": session, "timeframe": tf,
            "trend": trend, "ema20": round(ema_val, 2) if ema_val is not None else None, "status": status,
            "active_trade_type": t.trade_type if t else None,
            "active_symbol": t.option_symbol if t else None,
            "active_entry": t.entry_premium if t else None,
            "active_premium": round(live_premium, 2) if live_premium is not None else None,
            "active_sl": round(t.stop_loss, 2) if t else None,
            "active_target": t.target if t else None,
            "active_pnl_pts": pnl_pts,
            "active_pnl_rs": round(pnl_pts * config.LOT_SIZE, 2) if pnl_pts is not None else None,
            "trades_so_far": len(trader.closed_trades),
        })
        if write_files and len(bars_log) % 5 == 0:
            rows_so_far = [trade_to_row(t) for t in trader.closed_trades]
            write_live_state(bars_log, rows_so_far, current_meta())

    # ---------------- Session 1 ----------------
    bucket = []
    for _, row in session1.iterrows():
        spot = row["close"]
        bucket.append(row)
        if len(bucket) == config.TREND_FILTER_TIMEFRAME_MINUTES:
            engine.on_completed_15min_bar(bucket[-1]["close"])
            bucket = []

        status = "Waiting for Setup"
        live_premium = None
        if trader.has_open_position:
            live_premium, closed = _monitor_open_position(trader, engine, spot, row["datetime"], row["datetime"])
            status = "Trade Active" if trader.has_open_position else (closed.exit_reason if closed else "Trade Active")
        else:
            signal = engine.process_session1_candle(row["datetime"], row)
            if signal:
                trader.open(signal, row["datetime"], trade_date)
                status = "Trade Active"
            elif engine.call_pattern.is_armed or engine.put_pattern.is_armed:
                status = "Waiting for Breakout"

        log_bar(row["datetime"], spot, "Session 1", "1-min",
                engine.trend_filter.trend_for(spot), engine.trend_filter.value, status, live_premium)
        if delay:
            time.sleep(delay)

    # ---------------- Session 2 ----------------
    engine.call_pattern.reset()
    engine.put_pattern.reset()
    for _, row in session2.iterrows():
        spot = row["close"]
        status = "Waiting for Setup"
        live_premium = None
        if trader.has_open_position:
            bucket_end = row["datetime"] + timedelta(minutes=config.SESSION2_CANDLE_MINUTES - 1)
            live_premium, closed = _monitor_open_position(trader, engine, spot, row["datetime"], bucket_end)
            status = "Trade Active" if trader.has_open_position else (closed.exit_reason if closed else status)
        else:
            signal = engine.process_session2_candle(row["datetime"], row)
            if signal:
                trader.open(signal, row["datetime"], trade_date)
                status = "Trade Active"
            elif engine.call_pattern.is_armed or engine.put_pattern.is_armed:
                status = "Waiting for Breakout"

        log_bar(row["datetime"], spot, "Session 2", "3-min", "N/A", None, status, live_premium)
        if delay:
            time.sleep(delay)

    # ---------------- EOD square-off & reporting ----------------
    if len(today_1min):
        last_row = today_1min.iloc[-1]
        if trader.has_open_position:
            final_premium = _price_active(trader, last_row["close"], engine, last_row["datetime"])
            trader.force_close(last_row["datetime"], final_premium, "Market Close")

    rows = [trade_to_row(t) for t in trader.closed_trades]
    eod_stats = compute_eod_stats(rows, trade_date)
    meta = current_meta(eod_stats)

    csv_path = xlsx_path = None
    if write_files:
        rows, csv_path = write_csv(trader.closed_trades)
        xlsx_path = write_xlsx(rows, eod_stats)
        write_live_state(bars_log, rows, meta)

    state = {"meta": meta, "bars": bars_log, "trades": rows}
    return {"state": state, "closed_trades": trader.closed_trades, "eod_stats": eod_stats,
            "csv_path": csv_path, "xlsx_path": xlsx_path}


def run(trade_date=None, delay=0.0):
    """CLI entrypoint — runs build_state() with file writes enabled, prints
    the EOD summary, and returns (closed_trades, eod_stats) for callers that
    used the old interface (e.g. the backtest scripts)."""
    result = build_state(trade_date=trade_date, delay=delay, write_files=True)
    print(f"\nTrade log: {result['csv_path']}\nWorkbook:  {result['xlsx_path']}\nLive state: {config.LIVE_STATE_PATH}\n")
    for k, v in result["eod_stats"].items():
        print(f"  {k}: {v}")
    return result["closed_trades"], result["eod_stats"]


def _price_active(trader, spot, engine, now_dt):
    """Re-price the currently active trade's own strike for this bar (reuses
    engine.option_confirm's cached contract/historical-candle lookups instead
    of re-fetching them fresh on every bar)."""
    t = trader.active_trade
    contract = engine.option_confirm.price_strike(t.strike, t.trade_type, now_dt, spot)
    return contract["premium"]


def _monitor_open_position(trader, engine, spot, window_start_dt, window_end_dt):
    """Advance the open position's state across [window_start_dt, window_end_dt].

    In replay mode, steps through every real 1-minute option candle in that
    window and checks low-then-high each minute (conservative assumption:
    the adverse move happens before the favorable one within a bar) — this
    is what actually catches an intrabar stop-loss/trailing-stop touch that
    a single sparse sample per bar (e.g. once every 3 minutes for Session 2)
    would silently miss. Falls back to a single point check when real
    per-minute data isn't available (live mode, or synthetic data).
    Returns (last_premium, closed_trade_or_None).
    """
    t = trader.active_trade
    minute_candles = engine.option_confirm.ohlc_range(t.strike, t.trade_type, window_start_dt, window_end_dt)
    if minute_candles:
        last_premium = None
        for c in minute_candles:
            if not trader.has_open_position:
                break
            closed = trader.update(c["datetime"], c["low"])
            if closed:
                return c["low"], closed
            closed = trader.update(c["datetime"], c["high"])
            if closed:
                return c["high"], closed
            last_premium = c["close"]
        return last_premium, None

    live_premium = _price_active(trader, spot, engine, window_end_dt)
    closed = trader.update(window_end_dt, live_premium)
    return live_premium, closed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds between bars (for watching dashboard live)")
    args = parser.parse_args()

    d = date_cls.fromisoformat(args.date) if args.date else None
    run(trade_date=d, delay=args.delay)
