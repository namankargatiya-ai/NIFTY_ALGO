"""
Live trading executor: places REAL orders through broker/upstox_api.py.

STATUS: UNTESTED against a live Upstox account or the sandbox environment —
this sandbox has no network route to Upstox. Do not point this at real
capital until you have:
  1. Run it against Upstox's sandbox environment end-to-end.
  2. Confirmed LOT_SIZE, product type ("I" for intraday MIS), and margin
     requirements with your broker.

Orders are placed against `signal.instrument_key` / `trade.instrument_key` —
the real Upstox instrument key resolved by
`strategy/option_confirmation.py._resolve_contract()` (e.g. "NSE_FO|12345"),
not the human-readable trading symbol. `open()` refuses to place an order at
all when that key is None (no real contract was resolved for this strike —
e.g. no broker connected, or the strike isn't currently listed), since
placing a market order against a guessed symbol string can fail outright or,
worse, silently target the wrong contract. Market orders are irreversible.

To reduce the chance of an accidental live order, this class requires
`confirm_live=True` to be passed explicitly at construction — there is no
default that lets real orders fire silently.
"""
from typing import Optional
import config
from broker.upstox_api import UpstoxBroker
from orders.paper_trader import Trade


class LiveTrader:
    def __init__(self, broker: UpstoxBroker, confirm_live: bool = False):
        if not confirm_live:
            raise RuntimeError(
                "LiveTrader requires confirm_live=True to be passed explicitly. "
                "This places REAL orders with REAL money — make sure you mean it."
            )
        self.broker = broker
        self.active_trade: Optional[Trade] = None
        self.closed_trades = []
        self._entry_order_id = None

    @property
    def has_open_position(self):
        return self.active_trade is not None

    def open(self, signal, entry_time, trade_date):
        if self.has_open_position:
            return None

        if not signal.instrument_key:
            print(f"[live_trader] no real instrument_key resolved for {signal.symbol} "
                  f"— refusing to place a live order against a guessed symbol")
            return None

        response = self.broker.place_market_order(
            instrument_token=signal.instrument_key,
            transaction_type="BUY",
            quantity=config.LOT_SIZE * config.POSITION_SIZE_LOTS,
            product="I",
        )
        if response is None:
            print("[live_trader] order placement failed — not opening a position")
            return None

        self._entry_order_id = getattr(response.data, "order_id", None) if response else None

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
        """Fixed SL/target only — no trailing, no buffer. Exits the instant
        the premium touches either fixed level."""
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
        response = self.broker.place_market_order(
            instrument_token=t.instrument_key,
            transaction_type="SELL",
            quantity=config.LOT_SIZE * config.POSITION_SIZE_LOTS,
            product="I",
        )
        if response is None:
            print(f"[live_trader] WARNING: exit order failed for {t.option_symbol} "
                  f"— position may still be open at the broker. Check manually.")

        t.exit_time = current_time
        t.exit_premium = round(premium, 2)
        t.exit_reason = reason
        self.closed_trades.append(t)
        self.active_trade = None
        return t
