"""
Higher-timeframe trend filter used only in Session 1 (09:15-10:30).

Bullish: current NIFTY price is above the 20 EMA on the 15-minute timeframe.
Bearish: current NIFTY price is below the 20 EMA on the 15-minute timeframe.
"""
from indicators.ema import IncrementalEMA
import config


class TrendFilter:
    def __init__(self, seed_ema_value: float = None):
        self.ema20 = IncrementalEMA(period=config.EMA_PERIOD, seed_value=seed_ema_value)
        self._bar_count_in_bucket = 0
        self._current_value = seed_ema_value

    def on_completed_15min_bar(self, close_price: float) -> float:
        """Call this exactly once per completed 15-minute candle close."""
        self._current_value = self.ema20.update(close_price)
        return self._current_value

    def trend_for(self, spot_price: float) -> str:
        if self._current_value is None:
            return "Unknown"
        return "Bullish" if spot_price > self._current_value else "Bearish"

    @property
    def value(self):
        return self._current_value
