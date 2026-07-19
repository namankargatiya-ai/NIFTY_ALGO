"""
Upstox Connect (API v2) wrapper, built on the official `upstox-python-sdk`
package (`pip install upstox-python-sdk`, imported as `upstox_client`).

STATUS: This wrapper is written against the real, installed SDK's verified
method signatures (inspected directly from upstox_client v2.19 at build
time), but it has NOT been exercised against the live Upstox API or your
account — this sandbox has no network route to Upstox. Before running
`config.TRADING_MODE = "live"`:
  1. Test every method here against Upstox's *sandbox* environment first
     (see https://upstox.com/developer/api-documentation/sandbox).
  2. Double-check the option-chain response shape (`get_option_chain`) below
     against the current docs — auto-generated SDK models occasionally lag
     the real REST response, so this method defensively tries the SDK model
     first and falls back to a raw REST call if the expected fields are
     missing. Verify which path actually fires for your account/plan.
  3. Confirm `UNDERLYING_INSTRUMENT_KEY` and instrument-key formats for
     options (they're generated per-expiry and change every week) via
     `get_option_contracts`.

Auth flow (Upstox OAuth2, run once per day - access tokens expire daily):
    broker = UpstoxBroker()
    print(broker.get_login_url())          # open this in a browser, log in
    # Upstox redirects to UPSTOX_REDIRECT_URI with ?code=XYZ
    broker.exchange_code_for_token(code="XYZ")
    print(broker.access_token)              # save as UPSTOX_ACCESS_TOKEN
"""
from datetime import date
import requests

import upstox_client
from upstox_client.rest import ApiException

import config


class UpstoxBroker:
    def __init__(self, api_key=None, api_secret=None, redirect_uri=None, access_token=None):
        self.api_key = api_key or config.UPSTOX_API_KEY
        self.api_secret = api_secret or config.UPSTOX_API_SECRET
        self.redirect_uri = redirect_uri or config.UPSTOX_REDIRECT_URI
        self.access_token = access_token or config.UPSTOX_ACCESS_TOKEN
        self._configuration = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def get_login_url(self, state: str = "nifty_algo_trader") -> str:
        """URL to open in a browser to start the OAuth2 login flow."""
        return (
            "https://api.upstox.com/v2/login/authorization/dialog"
            f"?response_type=code&client_id={self.api_key}"
            f"&redirect_uri={self.redirect_uri}&state={state}"
        )

    def exchange_code_for_token(self, code: str) -> str:
        """Exchange the OAuth2 `code` (from the redirect_uri callback) for an access token."""
        login_api = upstox_client.LoginApi()
        response = login_api.token(
            api_version=config.UPSTOX_API_VERSION,
            code=code,
            client_id=self.api_key,
            client_secret=self.api_secret,
            redirect_uri=self.redirect_uri,
            grant_type="authorization_code",
        )
        self.access_token = response.access_token
        return self.access_token

    def _client(self):
        if self._configuration is None or self._configuration.access_token != self.access_token:
            self._configuration = upstox_client.Configuration()
            self._configuration.access_token = self.access_token
        return upstox_client.ApiClient(self._configuration)

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------
    def get_ltp(self, instrument_keys: list):
        api = upstox_client.MarketQuoteV3Api(self._client())
        try:
            resp = api.get_ltp(instrument_key=",".join(instrument_keys))
            return resp.data
        except ApiException as e:
            print(f"[upstox_api] get_ltp failed: {e}")
            return None

    def get_ohlc(self, instrument_keys: list, interval: str = "1d"):
        api = upstox_client.MarketQuoteV3Api(self._client())
        try:
            resp = api.get_market_quote_ohlc(interval=interval, instrument_key=",".join(instrument_keys))
            return resp.data
        except ApiException as e:
            print(f"[upstox_api] get_ohlc failed: {e}")
            return None

    def get_historical_candles(self, instrument_key: str, unit: str, interval: int, to_date: str):
        """unit: 'minutes' | 'hours' | 'days' | 'weeks' | 'months'; to_date: 'YYYY-MM-DD'."""
        api = upstox_client.HistoryV3Api(self._client())
        try:
            resp = api.get_historical_candle_data(instrument_key, unit, interval, to_date)
            return resp.data.candles  # each candle: [ts, open, high, low, close, volume, oi]
        except ApiException as e:
            print(f"[upstox_api] get_historical_candles failed: {e}")
            return None

    def get_intraday_candles(self, instrument_key: str, unit: str, interval: int):
        api = upstox_client.HistoryV3Api(self._client())
        try:
            resp = api.get_intra_day_candle_data(instrument_key, unit, interval)
            return resp.data.candles
        except ApiException as e:
            print(f"[upstox_api] get_intraday_candles failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------
    def get_option_contracts(self, instrument_key: str, expiry_date: str = None):
        """List available option contracts (instrument keys per strike) for an underlying."""
        api = upstox_client.OptionsApi(self._client())
        try:
            kwargs = {"expiry_date": expiry_date} if expiry_date else {}
            resp = api.get_option_contracts(instrument_key, **kwargs)
            return resp.data
        except ApiException as e:
            print(f"[upstox_api] get_option_contracts failed: {e}")
            return None

    def get_option_chain(self, instrument_key: str, expiry_date: str):
        """
        expiry_date format: 'YYYY-MM-DD'. Tries the SDK model first; falls
        back to a raw REST call if the expected nested fields aren't present
        (see module docstring, point 2 - unverified against a live account).
        """
        api = upstox_client.OptionsApi(self._client())
        try:
            resp = api.get_put_call_option_chain(instrument_key, expiry_date)
            data = resp.data
            if data and hasattr(data[0], "call_options"):
                return data
        except ApiException as e:
            print(f"[upstox_api] SDK get_option_chain failed, falling back to REST: {e}")

        return self._get_option_chain_raw(instrument_key, expiry_date)

    def _get_option_chain_raw(self, instrument_key: str, expiry_date: str):
        url = "https://api.upstox.com/v2/option/chain"
        headers = {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}
        params = {"instrument_key": instrument_key, "expiry_date": expiry_date}
        try:
            r = requests.get(url, headers=headers, params=params, timeout=10)
            r.raise_for_status()
            return r.json().get("data")
        except requests.RequestException as e:
            print(f"[upstox_api] raw option chain REST call failed: {e}")
            return None

    def get_option_ltp_and_delta(self, expiry_date: date, strike: float, option_type: str):
        """
        Returns {"premium": float, "delta": float} for the given strike/type
        by scanning the option chain, or None if not found / not available.
        Caller (strategy/option_confirmation.py) falls back to the
        Black-Scholes pricer when this returns None.
        """
        chain = self.get_option_chain(self.underlying_key(), expiry_date.isoformat())
        if not chain:
            return None

        for row in chain:
            row_strike = getattr(row, "strike_price", None) or (row.get("strike_price") if isinstance(row, dict) else None)
            if row_strike is None or float(row_strike) != float(strike):
                continue
            leg = getattr(row, "call_options", None) if option_type == "CALL" else getattr(row, "put_options", None)
            if leg is None and isinstance(row, dict):
                leg = row.get("call_options") if option_type == "CALL" else row.get("put_options")
            if leg is None:
                continue
            market_data = getattr(leg, "market_data", None) or (leg.get("market_data") if isinstance(leg, dict) else None)
            greeks = getattr(leg, "option_greeks", None) or (leg.get("option_greeks") if isinstance(leg, dict) else None)
            ltp = getattr(market_data, "ltp", None) or (market_data.get("ltp") if isinstance(market_data, dict) else None)
            delta = getattr(greeks, "delta", None) or (greeks.get("delta") if isinstance(greeks, dict) else None)
            if ltp is None:
                return None
            return {"premium": float(ltp), "delta": float(delta) if delta is not None else (0.5 if option_type == "CALL" else -0.5)}
        return None

    @staticmethod
    def underlying_key():
        return config.UNDERLYING_INSTRUMENT_KEY

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    def place_market_order(self, instrument_token: str, transaction_type: str, quantity: int,
                            product: str = "I", tag: str = "nifty_algo_trader"):
        """
        transaction_type: 'BUY' | 'SELL'. product: 'I' = intraday, 'D' = delivery.
        Returns the SDK's PlaceOrderV3Response, or None on failure.
        """
        api = upstox_client.OrderApiV3(self._client())
        body = upstox_client.PlaceOrderV3Request(
            quantity=quantity,
            product=product,
            validity="DAY",
            price=0,
            tag=tag,
            slice=False,
            instrument_token=instrument_token,
            order_type="MARKET",
            transaction_type=transaction_type,
            disclosed_quantity=0,
            trigger_price=0,
            is_amo=False,
        )
        try:
            return api.place_order(body)
        except ApiException as e:
            print(f"[upstox_api] place_market_order failed: {e}")
            return None

    def cancel_order(self, order_id: str):
        api = upstox_client.OrderApiV3(self._client())
        try:
            return api.cancel_order(order_id)
        except ApiException as e:
            print(f"[upstox_api] cancel_order failed: {e}")
            return None
