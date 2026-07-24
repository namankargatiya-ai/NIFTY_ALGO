"""
Paper trading executor: the tested, default path (config.TRADING_MODE =
"paper"). Simulates order fills at the resolved premium from
strategy/option_confirmation.py (real Upstox LTP if a broker session is
available, otherwise the Black-Scholes fallback) — no real orders are ever
placed.
"""
from dataclasses import dataclass, field
from typing import Optional
import config


@dataclass
class Trade:
    date: str
    entry_time: object
    trade_type: str
    strike: float
    option_symbol: str
    instrument_key: Optional[str]
    entry_premium: float
    stop_loss: float
    target: float
    exit_time: Optional[object] = None
    exit_premium: Optional[float] = None
    exit_reason: Optional[str] = None

    def pnl_points(self):
        if self.exit_premium is None:
            return None
        return round(self.exit_premium - self.entry_premium, 2)

    def pnl_rupees(self):
        pts = self.pnl_points()
        return None if pts is None else round(pts * config.LOT_SIZE, 2)

    def duration(self):
        if self.exit_time is None:
            return None
        return str(self.exit_time - self.entry_time)


class PaperTrader:
    def __init__(self):
        self.active_trade: Optional[Trade] = None
        self.closed_trades = []

    @property
    def has_open_position(self):
        return self.active_trade is not None

    def open(self, signal, entry_time, trade_date):
        if self.has_open_position:
            return None  # no pyramiding — refuse silently, caller should check has_open_position first
        trade = Trade(
            date=str(trade_date),
            entry_time=entry_time,
            trade_type=signal.trade_type,
            strike=signal.strike,
            option_symbol=signal.symbol,
            instrument_key=signal.instrument_key,
            entry_premium=signal.entry_premium,
            stop_loss=signal.stop_loss,
            target=signal.target,
        )
        self.active_trade = trade
        return trade

    def update(self, current_time, current_premium: float):
        """Feed the latest premium for the active trade. Returns the closed
        Trade if this update triggered an exit, else None.

        Fixed SL/target only — no trailing, no buffer. Exits the instant the
        premium touches either fixed level."""
        if not self.has_open_position:
            return None

        t = self.active_trade
        if current_premium <= t.stop_loss:
            return self._close(current_time, current_premium, "Stop Loss")
        if current_premium >= t.target:
            return self._close(current_time, current_premium, "Target")
        return None

    def force_close(self, current_time, current_premium: float, reason="Market Close"):
        if not self.has_open_position:
            return None
        return self._close(current_time, current_premium, reason)

    def _close(self, current_time, premium, reason):
        t = self.active_trade
        t.exit_time = current_time
        t.exit_premium = round(premium, 2)
        t.exit_reason = reason
        self.closed_trades.append(t)
        self.active_trade = None
        return t
