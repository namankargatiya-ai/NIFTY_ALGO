"""
Swing high/low detector (simple fractal method: a bar whose high/low is more
extreme than `lookback` bars on either side). Not required by the core
3-candle pullback rule, but useful as an extra confirmation layer or as an
alternative stop-loss reference point if you want to tighten/loosen the
14-point floor based on nearby market structure.
"""
from collections import deque


class SwingDetector:
    def __init__(self, lookback: int = 2):
        self.lookback = lookback
        self.window = deque(maxlen=2 * lookback + 1)
        self.last_swing_high = None
        self.last_swing_low = None

    def update(self, candle):
        """Feed one completed candle. Updates last_swing_high/last_swing_low
        when the center of the rolling window forms a fractal swing point."""
        self.window.append(candle)
        if len(self.window) < self.window.maxlen:
            return None

        mid_idx = self.lookback
        mid = self.window[mid_idx]
        others = [c for i, c in enumerate(self.window) if i != mid_idx]

        is_swing_high = all(mid["high"] >= c["high"] for c in others)
        is_swing_low = all(mid["low"] <= c["low"] for c in others)

        result = None
        if is_swing_high:
            self.last_swing_high = mid["high"]
            result = ("swing_high", mid["high"])
        if is_swing_low:
            self.last_swing_low = mid["low"]
            result = ("swing_low", mid["low"])
        return result

    def nearest_support(self):
        return self.last_swing_low

    def nearest_resistance(self):
        return self.last_swing_high
