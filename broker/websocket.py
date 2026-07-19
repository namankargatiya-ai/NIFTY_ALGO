"""
Live tick/quote streaming via Upstox's MarketDataStreamerV3 (protobuf feed,
handled internally by the SDK). STATUS: untested against a live connection
(no network route to Upstox from this sandbox) - verify against the sandbox
environment before relying on it for real trading.
"""
import upstox_client
import config


class LiveMarketFeed:
    def __init__(self, instrument_keys=None, mode="full", access_token=None, on_tick=None):
        """
        mode: 'ltpc' (LTP only, lightweight) | 'full' (full depth + OHLC) | 'option_greeks'
        on_tick: callback function invoked with each raw message from the feed.
        """
        self.instrument_keys = instrument_keys or [config.UNDERLYING_INSTRUMENT_KEY]
        self.mode = mode
        self.on_tick = on_tick or (lambda msg: print(msg))

        configuration = upstox_client.Configuration()
        configuration.access_token = access_token or config.UPSTOX_ACCESS_TOKEN
        self.streamer = upstox_client.MarketDataStreamerV3(
            upstox_client.ApiClient(configuration),
            self.instrument_keys,
            self.mode,
        )

    def start(self):
        self.streamer.on("message", self.on_tick)
        self.streamer.on("open", lambda: print("[websocket] Upstox market data feed connected"))
        self.streamer.on("error", lambda e: print(f"[websocket] error: {e}"))
        self.streamer.connect()   # blocking; run in its own thread/process from app.py

    def subscribe(self, instrument_keys, mode=None):
        self.streamer.subscribe(instrument_keys, mode or self.mode)

    def unsubscribe(self, instrument_keys):
        self.streamer.unsubscribe(instrument_keys)

    def disconnect(self):
        self.streamer.disconnect()


class PortfolioFeed:
    """Live order/position/holding update stream (separate from market data)."""
    def __init__(self, access_token=None, on_update=None):
        configuration = upstox_client.Configuration()
        configuration.access_token = access_token or config.UPSTOX_ACCESS_TOKEN
        self.streamer = upstox_client.PortfolioDataStreamer(
            upstox_client.ApiClient(configuration),
            order_update=True, position_update=True, holding_update=False, gtt_update=False,
        )
        self.on_update = on_update or (lambda msg: print(msg))

    def start(self):
        self.streamer.on("message", self.on_update)
        self.streamer.on("open", lambda: print("[websocket] Upstox portfolio feed connected"))
        self.streamer.connect()
