import json
import logging
import asyncio
from neo_api_client import NeoAPI
from app.core.settings import settings
from app.data.stream import data_stream
from app.execution.kotak import kotak_adapter

logger = logging.getLogger("MarketFeed")


class MarketFeed:
    """
    Manages WebSocket connection for Live Ticks.
    """

    def __init__(self):
        self.is_connected = False
        self.client = kotak_adapter.client
        self.subscribed_tokens = set()

    def on_message(self, message):
        """Callback from Kotak SDK."""
        # Parse and push to Stream
        # Kotak sends a list of dictionaries
        if isinstance(message, list):
            # Run in event loop because this callback might be synchronous
            loop = asyncio.get_event_loop()
            loop.create_task(data_stream.publish(message))

    def on_error(self, error):
        logger.error(f"❌ Feed Error: {error}")

    async def connect(self):
        """Starts the WebSocket."""
        if self.is_connected:
            return

        try:
            # Login must be completed by Execution layer first
            # But we can trigger a callback setup here
            self.client.on_message = self.on_message
            self.client.on_error = self.on_error
            self.client.on_close = lambda *args: logger.warning("🔌 Feed Disconnected")

            logger.info("📡 Market Feed Connected")
            self.is_connected = True
        except Exception as e:
            logger.critical(f"❌ Feed Connection Failed: {e}")

    async def subscribe(self, tokens: list):
        """
        Subscribes to a list of tokens.
        tokens: ['12345', '67890']
        """
        if not tokens:
            return

        # Filter already subscribed
        new_tokens = [t for t in tokens if t not in self.subscribed_tokens]
        if not new_tokens:
            return

        try:
            tokens_to_send = [
                {"instrument_token": t, "exchange_segment": "nse_cm"}
                for t in new_tokens
            ]

            # This SDK call is blocking, run in executor
            from app.core.executors import run_blocking

            await run_blocking(
                self.client.subscribe,
                instrument_tokens=tokens_to_send,
                isIndex=False,
                isCashWithFO=True,
            )

            self.subscribed_tokens.update(new_tokens)
            logger.info(f"🔔 Subscribed to {len(new_tokens)} tokens")
        except Exception as e:
            logger.error(f"❌ Subscribe Failed: {e}")


market_feed = MarketFeed()
