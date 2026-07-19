"""
Trailing stop: once a trade's premium reaches the minimum target, the stop
trails `config.TRAIL_POINTS` behind the highest premium seen so far, letting
the trade continue to run instead of capping profit at the minimum target.
"""
import config


class TrailingStopManager:
    def __init__(self, entry_premium: float, initial_target: float, initial_stop_loss: float):
        self.entry_premium = entry_premium
        self.target = initial_target
        self.stop_loss = initial_stop_loss
        self.highest_premium = entry_premium
        self.active = False

    def update(self, current_premium: float) -> str:
        """
        Feed the latest option premium. Returns one of:
          "hold", "target_trailing_started", "stop_loss", "trailing_stop"
        """
        self.highest_premium = max(self.highest_premium, current_premium)

        if current_premium >= self.target and not self.active:
            self.active = True

        if self.active:
            trail_level = round(self.highest_premium - config.TRAIL_POINTS, 2)
            self.stop_loss = max(self.stop_loss, trail_level)

        if current_premium <= self.stop_loss:
            return "trailing_stop" if self.active else "stop_loss"

        return "hold"
