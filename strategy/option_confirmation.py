"""
Given a breakout side (CALL/PUT) and the current spot price, this module:
  1. Selects the nearest ITM strike (one step ITM from ATM).
  2. Resolves the REAL tradable contract (instrument_key, trading_symbol)
     via broker/upstox_api.py's get_option_contracts — not a hand-built
     OCC-style guess, which can silently reference a contract that doesn't
     exist if the assumed expiry weekday is wrong.
  3. Prices it:
       - Live session (replay_date is None): real Upstox option-chain LTP.
       - Historical replay (replay_date set, e.g. market closed today and
         we're using the last completed session): real per-minute option
         candles for that exact contract via get_historical_candles — the
         option-chain endpoint is live-only and returns nothing for a past
         session, so this is what actually gets a genuine historical price
         instead of a synthetic one.
       - Black-Scholes fallback only if neither real source has data.

This is the "option confirmation" step the folder name implies: a trade is
only actually opened once we know a real (or clearly-labeled simulated)
premium and delta for the specific contract we'd trade.
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
                being replayed (e.g. market closed right now) — triggers
                the real historical-option-candle pricing path instead of
                the live-only option chain.
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
        """Real 1-min OHLC candles for this contract on replay_date, sorted
        chronologically and cached per contract."""
        if instrument_key in self._ohlc_by_key:
            return self._ohlc_by_key[instrument_key]
        try:
            candles = self.broker.get_historical_candles(
                instrument_key, "minutes", 1, self.replay_date.isoformat()
            )
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
        sample point per bar. Only available in replay mode with a real
        contract resolved; returns [] otherwise (caller should fall back to
        price_strike())."""
        if self.broker is None or self.replay_date is None:
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
        """
        contract = self._resolve_contract(strike, opt_type)
        symbol = contract.trading_symbol if contract else self._fallback_symbol(strike, opt_type)

        if self.broker is not None and self.replay_date is None:
            live = self.broker.get_option_ltp_and_delta(
                expiry_date=self.expiry_dt.date(), strike=strike, option_type=opt_type
            )
            if live is not None:
                live["strike"] = strike
                live["symbol"] = symbol
                live["source"] = "upstox_live"
                return live

        if self.broker is not None and self.replay_date is not None and contract is not None:
            premium = self._historical_premium(contract.instrument_key, now_dt)
            if premium is not None:
                # Real historical premium, but Upstox's historical candles carry
                # no greeks — delta is Black-Scholes-estimated purely for risk
                # sizing, it does not affect the (real) premium returned here.
                T = time_to_expiry_years(now_dt, self.expiry_dt)
                _, delta = bs_price_delta(spot, strike, T, config.DEFAULT_IV, opt_type)
                return {
                    "strike": strike, "symbol": symbol, "premium": round(premium, 2),
                    "delta": delta, "source": "upstox_historical",
                }

        # Fallback: Black-Scholes theoretical price
        T = time_to_expiry_years(now_dt, self.expiry_dt)
        premium, delta = bs_price_delta(spot, strike, T, config.DEFAULT_IV, opt_type)
        return {
            "strike": strike,
            "symbol": symbol,
            "premium": round(premium, 2),
            "delta": delta,
            "source": "black_scholes_fallback",
        }
