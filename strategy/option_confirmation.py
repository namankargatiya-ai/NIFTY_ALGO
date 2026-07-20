"""
Given a breakout side (CALL/PUT) and the current spot price, this module:
  1. Selects the nearest ITM strike (one step ITM from ATM).
  2. Resolves the REAL tradable contract (instrument_key, trading_symbol)
     via broker/upstox_api.py's get_option_contracts — not a hand-built
     OCC-style guess, which can silently reference a contract that doesn't
     exist if the assumed expiry weekday is wrong.
  3. Prices it, in this order:
       - Real per-minute option candle for the exact timestamp being priced,
         via get_historical_candles (replaying a past session) or
         get_intraday_candles (today's still-accumulating session). Tried
         first, and preferred whenever available, because it's stable across
         re-runs — see the IMPORTANT note below.
       - Live Upstox option-chain LTP, only when no candle exists yet for
         that timestamp (i.e. it's the current, still-forming minute).
       - Black-Scholes fallback only if neither real source has data.

This is the "option confirmation" step the folder name implies: a trade is
only actually opened once we know a real (or clearly-labeled simulated)
premium and delta for the specific contract we'd trade.

IMPORTANT — why every already-completed minute is priced from real per-minute
candles instead of the live quote: app.py's build_state() re-runs the whole
session from market open through "now" on every call (every dashboard
refresh). If pricing used the live option-chain LTP for bars that are
actually in the past, an already-opened trade's entry premium (and the
stop-loss/target derived from it) would silently change on every refresh,
tracking whatever the live premium happens to be *right now* instead of what
it genuinely was at the moment of entry. Per-minute candles are stable across
reruns, so only the current, still-forming minute (which has no candle yet)
falls back to the live quote.
"""
from datetime import datetime
import config
from utils.option_pricer import bs_price_delta, time_to_expiry_years, nearest_itm_strikes


class OptionConfirmation:
    def __init__(self, broker=None, expiry_dt=None, replay_date=None):
        """
        broker: an instance of broker.upstox_api.UpstoxBroker, or None to
                always use the synthetic Black-Scholes fallback (paper mode
                without a live connection).
        replay_date: set when "today" is actually a past completed session
                being replayed (e.g. market closed right now) — selects
                get_historical_candles() (past sessions) vs
                get_intraday_candles() (today, still accumulating) as the
                source of real per-minute option candles. Either way, any
                minute that already has a candle is priced from it, not from
                the live quote — see module docstring.
        """
        self.broker = broker
        self.expiry_dt = expiry_dt
        self.replay_date = replay_date
        self._contracts_by_strike_type = {}   # (strike, opt_type) -> contract, cached per expiry
        self._contracts_loaded = False
        self._ohlc_by_key = {}  # instrument_key -> sorted list of {datetime, open, high, low, close}, cached per contract

    def _load_contracts(self):
        if self._contracts_loaded or self.broker is None:
            return
        self._contracts_loaded = True
        try:
            contracts = self.broker.get_option_contracts(
                config.UNDERLYING_INSTRUMENT_KEY, expiry_date=self.expiry_dt.date().isoformat()
            )
        except Exception as e:
            print(f"[option_confirmation] get_option_contracts failed: {e}")
            contracts = None
        for c in (contracts or []):
            self._contracts_by_strike_type[(float(c.strike_price), c.instrument_type)] = c

    def _resolve_contract(self, strike, opt_type):
        """Real Upstox contract for this strike/type, or None if unavailable
        (e.g. no broker, or this exact strike isn't currently listed)."""
        self._load_contracts()
        opt_code = "CE" if opt_type == "CALL" else "PE"
        return self._contracts_by_strike_type.get((float(strike), opt_code))

    def _fallback_symbol(self, strike, opt_type):
        """Only used when no real contract can be resolved (no broker, or
        lookup failed) — a best-effort label, not a guaranteed-real symbol."""
        exp_str = self.expiry_dt.strftime("%d%b%y").upper()
        return f"NIFTY{exp_str}{int(strike)}{'CE' if opt_type == 'CALL' else 'PE'}"

    def _load_historical_ohlc(self, instrument_key):
        """Real 1-min OHLC candles for this contract, sorted chronologically
        and cached per contract. Uses the historical endpoint for a past
        replayed session (replay_date set), or the intraday endpoint for
        today's still-accumulating session (replay_date is None) — same
        split app.py's load_day_data() uses for the underlying."""
        if instrument_key in self._ohlc_by_key:
            return self._ohlc_by_key[instrument_key]
        try:
            if self.replay_date is not None:
                candles = self.broker.get_historical_candles(
                    instrument_key, "minutes", 1, self.replay_date.isoformat()
                )
            else:
                candles = self.broker.get_intraday_candles(instrument_key, "minutes", 1)
        except Exception as e:
            print(f"[option_confirmation] historical option candles failed: {e}")
            candles = None
        rows = [
            {"datetime": datetime.fromisoformat(c[0]).replace(tzinfo=None, second=0, microsecond=0),
             "open": c[1], "high": c[2], "low": c[3], "close": c[4]}
            for c in (candles or [])
        ]
        rows.sort(key=lambda r: r["datetime"])
        self._ohlc_by_key[instrument_key] = rows
        return rows

    def _historical_premium(self, instrument_key, now_dt):
        """Real 1-min close price for this contract at/just before now_dt."""
        rows = self._load_historical_ohlc(instrument_key)
        target = now_dt.replace(second=0, microsecond=0)
        candidates = [r for r in rows if r["datetime"] <= target]
        return float(candidates[-1]["close"]) if candidates else None

    def ohlc_range(self, strike: float, opt_type: str, start_dt: datetime, end_dt: datetime):
        """Real 1-min OHLC candles for this strike/type between start_dt and
        end_dt inclusive, chronological. Used to check every real intrabar
        high/low while a position is open, instead of a single sparse
        sample point per bar. Only available with a real contract resolved
        and a broker connected; returns [] otherwise (caller should fall
        back to price_strike())."""
        if self.broker is None:
            return []
        contract = self._resolve_contract(strike, opt_type)
        if contract is None:
            return []
        rows = self._load_historical_ohlc(contract.instrument_key)
        start = start_dt.replace(second=0, microsecond=0)
        end = end_dt.replace(second=0, microsecond=0)
        return [r for r in rows if start <= r["datetime"] <= end]

    def confirm(self, now_dt: datetime, spot: float, opt_type: str):
        """Entry-time: derive the nearest-ITM strike fresh from spot, then price it."""
        call_strike, put_strike = nearest_itm_strikes(spot)
        strike = call_strike if opt_type == "CALL" else put_strike
        return self.price_strike(strike, opt_type, now_dt, spot)

    def price_strike(self, strike: float, opt_type: str, now_dt: datetime, spot: float):
        """
        Re-price a specific, already-known strike (used every bar for the
        active trade — always the strike actually held, not re-derived from
        the current spot). Returns a dict: {strike, symbol, premium, delta, source}.

        Tries the real per-minute candle for now_dt FIRST — that price is
        fixed once the candle has printed, so replaying the same bar later
        (e.g. on the next dashboard refresh) always reproduces the same
        premium. Only falls back to the live option-chain quote when no
        candle exists yet for now_dt (the current, still-forming minute) —
        see module docstring for why this ordering matters.

        `instrument_key` in the returned dict is Upstox's real tradable
        instrument key (e.g. "NSE_FO|12345") when a real contract was
        resolved, or None when we fell back to a guessed symbol (no broker,
        or this exact strike isn't currently listed) — callers that place
        real orders (orders/live_trader.py) must refuse to trade when this
        is None, since there is nothing real to place an order against.
        """
        contract = self._resolve_contract(strike, opt_type)
        symbol = contract.trading_symbol if contract else self._fallback_symbol(strike, opt_type)
        instrument_key = contract.instrument_key if contract else None

        if self.broker is not None and contract is not None:
            premium = self._historical_premium(contract.instrument_key, now_dt)
            if premium is not None:
                # Real historical premium, but Upstox's historical candles carry
                # no greeks — delta is Black-Scholes-estimated purely for risk
                # sizing, it does not affect the (real) premium returned here.
                T = time_to_expiry_years(now_dt, self.expiry_dt)
                _, delta = bs_price_delta(spot, strike, T, config.DEFAULT_IV, opt_type)
                return {
                    "strike": strike, "symbol": symbol, "instrument_key": instrument_key,
                    "premium": round(premium, 2), "delta": delta,
                    "source": "upstox_historical" if self.replay_date is not None else "upstox_intraday",
                }

        if self.broker is not None:
            live = self.broker.get_option_ltp_and_delta(
                expiry_date=self.expiry_dt.date(), strike=strike, option_type=opt_type
            )
            if live is not None:
                live["strike"] = strike
                live["symbol"] = symbol
                live["instrument_key"] = instrument_key
                live["source"] = "upstox_live"
                return live

        # Fallback: Black-Scholes theoretical price
        T = time_to_expiry_years(now_dt, self.expiry_dt)
        premium, delta = bs_price_delta(spot, strike, T, config.DEFAULT_IV, opt_type)
        return {
            "strike": strike,
            "symbol": symbol,
            "instrument_key": instrument_key,
            "premium": round(premium, 2),
            "delta": delta,
            "source": "black_scholes_fallback",
        }
