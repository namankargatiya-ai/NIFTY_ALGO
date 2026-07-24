"""
Risk management rules:
  - Fixed SL: always exactly 14 option premium points (config.MIN_SL_POINTS).
    No buffer and no widening beyond this — the technical (candle-range) SL
    is used only to decide whether to skip a trade, never to size the SL.
  - If the technical SL exceeds MAX_ACCEPTABLE_RISK_POINTS, the trade is
    skipped (configurable).
  - Fixed target: always exactly 15 option premium points
    (config.MIN_TARGET_POINTS).
  - No trailing stop loss — a trade exits only at the fixed SL or the fixed
    target (see orders/paper_trader.py / orders/live_trader.py).
  - Fixed position size, no pyramiding (enforced by orders/paper_trader.py /
    orders/live_trader.py refusing a new entry while a position is open).
"""
import config


class RiskDecision:
    def __init__(self, approved, stop_loss=None, target=None, reason=None):
        self.approved = approved
        self.stop_loss = stop_loss
        self.target = target
        self.reason = reason


def size_stop_and_target(entry_premium: float, underlying_range_points: float, option_delta: float) -> RiskDecision:
    technical_sl_pts = abs(underlying_range_points * option_delta)

    if technical_sl_pts > config.MAX_ACCEPTABLE_RISK_POINTS:
        return RiskDecision(
            approved=False,
            reason=f"technical SL {technical_sl_pts:.1f} pts exceeds max acceptable risk "
                    f"{config.MAX_ACCEPTABLE_RISK_POINTS} pts"
        )

    stop_loss = round(entry_premium - config.MIN_SL_POINTS, 2)
    target = round(entry_premium + config.MIN_TARGET_POINTS, 2)
    return RiskDecision(approved=True, stop_loss=stop_loss, target=target)


def position_size_lots():
    return config.POSITION_SIZE_LOTS if not config.ALLOW_PYRAMIDING else config.POSITION_SIZE_LOTS
