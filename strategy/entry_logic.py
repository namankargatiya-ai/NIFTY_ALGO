"""
Orchestrates the full entry decision for one completed candle:
  1. (Session 1 only) check the 15-min EMA trend filter.
  2. Feed the candle to the relevant 3-candle pullback pattern tracker(s).
  3. On a breakout, resolve the real/simulated option contract (strike,
     symbol, premium, delta) via strategy/option_confirmation.py.
  4. Size the stop-loss/target via risk/risk_manager.py.

Returns a TradeSignal only when every step succeeds; the caller (orders/
paper_trader.py or orders/live_trader.py) is responsible for actually
opening the position and for refusing new entries while one is already open
(no pyramiding, one trade at a time, per config.ALLOW_PYRAMIDING).
"""
from dataclasses import dataclass
from typing import Optional

from strategy.candle_pattern import PullbackPattern
from strategy.trend_filter import TrendFilter
from strategy.option_confirmation import OptionConfirmation


@dataclass
class TradeSignal:
    trade_type: str          # CALL / PUT
    strike: float
    symbol: str
    instrument_key: Optional[str]  # real Upstox instrument key, or None if no real contract was resolved
    entry_premium: float
    stop_loss: float
    target: float
    source: str               # "upstox_live" | "upstox_historical" | "black_scholes_fallback"


class EntryEngine:
    def __init__(self, expiry_dt, broker=None, seed_ema_value=None, replay_date=None):
        self.trend_filter = TrendFilter(seed_ema_value=seed_ema_value)
        self.call_pattern = PullbackPattern("CALL_SETUP")
        self.put_pattern = PullbackPattern("PUT_SETUP")
        self.option_confirm = OptionConfirmation(broker=broker, expiry_dt=expiry_dt, replay_date=replay_date)

    def on_completed_15min_bar(self, close_price: float):
        """Feed Session-1 15-min bars to keep the EMA trend filter current."""
        return self.trend_filter.on_completed_15min_bar(close_price)

    def process_session1_candle(self, now_dt, candle) -> Optional[TradeSignal]:
        from risk.risk_manager import size_stop_and_target

        spot = candle["close"]
        trend = self.trend_filter.trend_for(spot)

        if trend == "Bullish":
            self.put_pattern.reset()
            rng = self.call_pattern.range_points()
            if self.call_pattern.process(candle):
                signal = self._try_open(now_dt, spot, "CALL", rng, size_stop_and_target)
                self.call_pattern.reset()
                return signal
        elif trend == "Bearish":
            self.call_pattern.reset()
            rng = self.put_pattern.range_points()
            if self.put_pattern.process(candle):
                signal = self._try_open(now_dt, spot, "PUT", rng, size_stop_and_target)
                self.put_pattern.reset()
                return signal
        return None

    def process_session2_candle(self, now_dt, candle) -> Optional[TradeSignal]:
        from risk.risk_manager import size_stop_and_target

        spot = candle["close"]

        call_rng = self.call_pattern.range_points()
        if self.call_pattern.process(candle):
            signal = self._try_open(now_dt, spot, "CALL", call_rng, size_stop_and_target)
            self.call_pattern.reset()
            if signal:
                return signal

        put_rng = self.put_pattern.range_points()
        if self.put_pattern.process(candle):
            signal = self._try_open(now_dt, spot, "PUT", put_rng, size_stop_and_target)
            self.put_pattern.reset()
            if signal:
                return signal

        return None

    def _try_open(self, now_dt, spot, opt_type, range_points, size_stop_and_target) -> Optional[TradeSignal]:
        contract = self.option_confirm.confirm(now_dt, spot, opt_type)
        if contract is None:
            return None

        decision = size_stop_and_target(contract["premium"], range_points, contract["delta"])
        if not decision.approved:
            return None  # e.g. technical SL too wide -> skip, per risk rules

        return TradeSignal(
            trade_type=opt_type,
            strike=contract["strike"],
            symbol=contract["symbol"],
            instrument_key=contract["instrument_key"],
            entry_premium=contract["premium"],
            stop_loss=decision.stop_loss,
            target=decision.target,
            source=contract["source"],
        )
