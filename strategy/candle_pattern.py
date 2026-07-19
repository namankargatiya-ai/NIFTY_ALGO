"""
3-candle pullback pattern detector.

CALL setup: exactly 3 consecutive bearish (red) candles -> mark their highest
            high as the armed level -> breakout above that level triggers a
            CALL entry.
PUT setup:  exactly 3 consecutive bullish (green) candles -> mark their
            lowest low as the armed level -> breakout below that level
            triggers a PUT entry.

Once armed, the level is frozen and stays live through any number of
intervening candles of any color — it only clears on an actual breakout, or
when the caller explicitly calls reset() (e.g. after a trade fires, or at a
session boundary). Only the initial 3-candle formation requires strict
consecutive same-color candles; a non-matching candle before arming resets
the buffer, but does nothing once armed.

This module only tracks pattern state on a stream of completed candles; it
knows nothing about trend filters, options, or order placement (see
strategy/entry_logic.py for the orchestration).
"""


def _is_red(candle):
    return candle["close"] < candle["open"]


def _is_green(candle):
    return candle["close"] > candle["open"]


class PullbackPattern:
    def __init__(self, kind: str):
        """kind: 'CALL_SETUP' (needs 3 red candles) or 'PUT_SETUP' (needs 3 green candles)."""
        assert kind in ("CALL_SETUP", "PUT_SETUP")
        self.kind = kind
        self.buffer = []
        self.armed_level = None

    def reset(self):
        self.buffer = []
        self.armed_level = None

    def range_points(self):
        if not self.buffer:
            return None
        return max(c["high"] for c in self.buffer) - min(c["low"] for c in self.buffer)

    def process(self, candle) -> bool:
        """
        Feed one completed candle. Returns True if this candle is a breakout
        that should trigger an entry (caller is responsible for opening the
        trade and then calling reset()).
        """
        if self.armed_level is not None:
            # Armed level is frozen — stays live regardless of intervening
            # candle colors, only a breakout (or an explicit reset()) clears it.
            return (candle["high"] > self.armed_level) if self.kind == "CALL_SETUP" \
                else (candle["low"] < self.armed_level)

        is_match = _is_red(candle) if self.kind == "CALL_SETUP" else _is_green(candle)
        if is_match:
            self.buffer.append(candle)
            if len(self.buffer) > 3:
                self.buffer.pop(0)
            if len(self.buffer) == 3:
                if self.kind == "CALL_SETUP":
                    self.armed_level = max(c["high"] for c in self.buffer)
                else:
                    self.armed_level = min(c["low"] for c in self.buffer)
        else:
            self.buffer = []

        return False

    @property
    def is_armed(self):
        return self.armed_level is not None
