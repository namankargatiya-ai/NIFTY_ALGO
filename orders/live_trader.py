"""
Live trading executor: places REAL orders through broker/upstox_api.py.

STATUS: UNTESTED against a live Upstox account or the sandbox environment —
this sandbox has no network route to Upstox. Do not point this at real
capital until you have:
  1. Run it against Upstox's sandbox environment end-to-end.
  2. Verified `strategy/option_confirmation.py` resolves the correct, currently
     tradable `instrument_token` for each contract (this module currently
     reuses the OCC-style symbol as a placeholder `instrument_token` — you
     MUST replace this with the real Upstox instrument key from
     `broker.get_option_contracts()`, e.g. "NSE_FO|12345", before placing
     real orders. Market orders are irreversible.
  2. Confirmed LOT_SIZE, product type ("I" for intraday MIS), and margin
     requirements with your broker.

To reduce the chance of an accidental live order, this class requires
`confirm_live=True` to be passed explicitly at construction — there is no
default that lets real orders fire silently.
"""
from typing import Optional
import config
from broker.upstox_api import UpstoxBroker
from orders.paper_trader import Trade
from risk.trailing_stop import TrailingStopManager


class LiveTrader:
    def __init__(self, broker: UpstoxBroker, confirm_live: bool = False):
        if not confirm_live:
            raise RuntimeError(
                "LiveTrader requires confirm_live=True to be passed explicitly. "
                "This places REAL orders with REAL money — make sure you mean it."
            )
        self.broker = broker
        self.active_trade: Optional[Trade] = None
        self._trailing: Optional[TrailingStopManager] = None
        self.closed_trades = []
        self._entry_order_id = None

    @property
    def has_open_position(self):
        return self.active_trade is not None

    def open(self, signal, entry_time, trade_date):
        if self.has_open_position:
            return None

        # TODO: replace `signal.symbol` with the real Upstox instrument_token
        # for this contract — see module docstring. Placing an order against
        # an OCC-style symbol string instead of a real instrument key WILL FAIL.
        response = self.broker.place_market_order(
            instrument_token=signal.symbol,
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
            entry_premium=signal.entry_premium,
            stop_loss=signal.stop_loss,
            target=signal.target,
        )
        self.active_trade = trade
        self._trailing = TrailingStopManager(signal.entry_premium, signal.target, signal.stop_loss)
        return trade

    def update(self, current_time, current_premium: float):
        if not self.has_open_position:
            return None
        result = self._trailing.update(current_premium)
        self.active_trade.stop_loss = self._trailing.stop_loss

        if result in ("stop_loss", "trailing_stop"):
            return self._close(current_time, current_premium,
                                "Stop Loss" if result == "stop_loss" else "Trailing Stop")
        return None

    def force_close(self, current_time, current_premium: float, reason="Market Close"):
        if not self.has_open_position:
            return None
        return self._close(current_time, current_premium, reason)

    def _close(self, current_time, premium, reason):
        t = self.active_trade
        response = self.broker.place_market_order(
            instrument_token=t.option_symbol,
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
        self._trailing = None
        return t
