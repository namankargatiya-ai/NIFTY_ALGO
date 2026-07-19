"""EMA (Exponential Moving Average) indicator utilities."""
import pandas as pd


def ema(series: pd.Series, period: int = 20) -> pd.Series:
    """Standard EMA over a pandas Series of closes."""
    return series.ewm(span=period, adjust=False).mean()


class IncrementalEMA:
    """
    Streaming EMA updater for live/paper trading, where candles arrive one at
    a time rather than as a pre-loaded DataFrame.
    """
    def __init__(self, period: int = 20, seed_value: float = None):
        self.period = period
        self.alpha = 2 / (period + 1)
        self.value = seed_value

    def update(self, close_price: float) -> float:
        if self.value is None:
            self.value = close_price
        else:
            self.value = close_price * self.alpha + self.value * (1 - self.alpha)
        return self.value
