# Nifty Algo Trader

A modular intraday options algo-trading system for NIFTY: multi-timeframe
trend filter + 3-candle pullback/breakout entries, fixed risk management
with a trailing stop, full trade logging/EOD reporting, a live-style
Streamlit dashboard, and a real (but unverified-live) Upstox Connect broker
integration.

## Quick start (paper trading — safe, needs no credentials)

```bash
pip install -r requirements.txt
python app.py                       # runs one simulated trading day, paper mode
streamlit run dashboard/dashboard.py  # in a second terminal, view the results
```

Run `python app.py --delay 0.05` and open the dashboard in parallel to watch
it "replay" bar-by-bar like a live session.

## Project layout

```
app.py                  Main entrypoint — wires data → strategy → risk → orders → reports
config.py               All tunables: credentials (via env), session times, risk params, paths

broker/
  upstox_api.py          Real Upstox Connect wrapper (auth, quotes, candles, option chain, orders)
  websocket.py            Live market-data / portfolio websocket streams (MarketDataStreamerV3)

strategy/
  trend_filter.py         15-min 20-EMA trend filter (Session 1 only)
  candle_pattern.py        3-candle pullback pattern detector (CALL/PUT setups)
  swing_detector.py        Fractal swing high/low utility (optional extra confirmation)
  option_confirmation.py   Resolves nearest-ITM contract + real/simulated premium
  entry_logic.py            Orchestrates the above into a single entry decision (TradeSignal)

indicators/
  ema.py                   EMA math (batch + streaming)

risk/
  risk_manager.py          SL/target sizing, max-risk skip rule
  trailing_stop.py          Trailing stop state machine

orders/
  paper_trader.py           Simulated executor (default, safe, fully tested)
  live_trader.py             REAL order executor via broker/upstox_api.py (see warnings below)

reports/
  trade_logger.py           CSV/XLSX trade log export
  performance.py             EOD performance report calculation

dashboard/
  dashboard.py               Streamlit live/replay dashboard

utils/
  option_pricer.py           Black-Scholes fallback pricer + nearest-ITM strike calc
  synthetic_data.py           Synthetic NIFTY data generator (fallback when no broker connected)

logs/, data/, exports/    Runtime output (live_state.json, trade_log.csv/xlsx) — gitignored
```

## Modes

Set in `config.py` / via env var `TRADING_MODE`:
- **`paper`** (default): `orders/paper_trader.py` simulates fills. Uses real Upstox
  option-chain LTPs if `UPSTOX_ACCESS_TOKEN` is set, otherwise a Black-Scholes
  fallback (`utils/option_pricer.py`) on synthetic data (`utils/synthetic_data.py`).
- **`live`**: `orders/live_trader.py` places REAL orders. **Read the warnings in
  that file before ever using this** — it requires `confirm_live=True` explicitly,
  and the option instrument-token wiring is marked with a TODO you must resolve
  first (see below).

## ⚠️ Before going anywhere near live trading

This was built in a sandboxed environment with **no network route to Upstox**,
so the broker integration is written against the real, installed
`upstox-python-sdk`'s verified method signatures, but has **never been
exercised against a live account or Upstox's sandbox environment**. Before
`TRADING_MODE=live`:

1. **Test on Upstox's sandbox environment first** (see their API docs' Sandbox
   section) — never point a first run at real capital.
2. **Fix the instrument-token TODO** in `orders/live_trader.py` — it currently
   passes the human-readable option symbol (e.g. `NIFTY23JUL2624950CE`) as the
   order's `instrument_token`, but Upstox needs the real generated instrument
   key (e.g. `NSE_FO|12345`) from `broker.get_option_contracts()`. Placing an
   order with the wrong token will fail or, worse, silently target the wrong
   contract.
3. **Verify the option-chain response shape** in `broker/upstox_api.py
   get_option_chain()` — auto-generated SDK models can lag the real REST API;
   this method defensively tries the SDK model then falls back to a raw REST
   call, but you should confirm which path fires for your account and that
   the parsed premium/delta are correct.
4. Confirm `config.LOT_SIZE`, product type (`"I"` = intraday MIS), and margin
   requirements directly with Upstox/your broker — lot sizes change.
5. Get comfortable with `paper` mode first, ideally across several real
   trading days, before touching `live`.

## Key assumptions (all centralized in `config.py`)
- Lot size: 75 — **confirm against the current NSE-published lot size.**
- Strike step: 50; nearest ITM = one step ITM from ATM.
- Flat IV (13%) and risk-free rate (7%) for the Black-Scholes fallback — no
  smile/skew, since that needs a live option chain.
- Expiry treated as the next Thursday (NIFTY weekly convention).
- Technical SL = 3-candle pattern's underlying range × option delta at entry,
  floored at 14 premium points; trades needing a wider stop than 60 points
  are skipped (configurable).
- Trailing stop: 10 points behind the highest premium once the 15-point
  minimum target is hit.
- Brokerage estimate: ₹40/round-trip flat placeholder — replace with your
  actual broker's charges in `config.ESTIMATED_CHARGES_PER_TRADE`.

## Extending

- **Real-time execution**: today `app.py` runs one full day's worth of bars
  in a loop (from historical/intraday candles or synthetic data). To go
  truly real-time, replace the bar loop with `broker/websocket.py`'s
  `LiveMarketFeed`, aggregating ticks into 1-min/3-min candles yourself and
  calling `strategy/entry_logic.py` per completed candle — the strategy,
  risk, and order-execution code doesn't need to change.
- **Swing-based stops**: `strategy/swing_detector.py` is wired in but not yet
  consumed by `risk/risk_manager.py` — hook it in there if you want structure-
  based (rather than purely delta-scaled) stop placement.
