import asyncio
import logging

from app.core.executors import run_blocking
from app.data.stream import data_stream
from app.execution.kotak import kotak_adapter

logger = logging.getLogger("MarketFeed")


class MarketFeed:
    """
    Self-Healing WebSocket Client.
    """

    def __init__(self):
        self.client = kotak_adapter.client
        self.subscribed_tokens = set()
        self.reconnect_delay = 2  # Start with 2 seconds
        self._stop_event = asyncio.Event()

    def on_message(self, message):
        """Raw callback from thread."""
        if isinstance(message, list):
            # Fire and forget into the Event Bus
            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                loop.create_task(data_stream.publish(message))

    def on_error(self, error):
        logger.error(f"❌ Feed Error: {error}")

    def on_close(self, message):
        logger.warning("🔌 Feed Disconnected! Initiating Reconnect...")
        # The watchdog loop will handle the reconnection logic

    async def connect(self):
        """
        Starts the connection loop.
        """
        self.client.on_message = self.on_message
        self.client.on_error = self.on_error
        self.client.on_close = self.on_close

        while not self._stop_event.is_set():
            try:
                # 1. Login Check
                if not kotak_adapter.is_logged_in:
                    logger.info("Waiting for Broker Login...")
                    await asyncio.sleep(2)
                    continue

                # 2. Connect (Blocking Call)
                # We check connectivity via a property or just try subscribing
                # Kotak SDK doesn't expose 'is_connected' easily, so we rely on re-subscribe

                logger.info("📡 Connecting to Kotak Feed...")
                # Note: Kotak's 'subscribe' implicitly connects if not connected
                if self.subscribed_tokens:
                    await self.subscribe(list(self.subscribed_tokens))

                # Reset backoff on success
                self.reconnect_delay = 2

                # Wait until something breaks
                # We can use a specialized loop or just sleep large intervals
                # ideally, the SDK blocks, but here we likely need a keep-alive check.
                await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"⚠️ Feed Connection Failed: {e}")
                logger.info(f"⏳ Retrying in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, 60)  # Cap at 60s

    async def subscribe(self, tokens: list):
        """
        Subscribes to tokens and updates the internal set for reconnection.
        """
        if not tokens:
            return

        # Update intent (even if connection is down, we want to subscribe when it comes up)
        self.subscribed_tokens.update(tokens)

        try:
            tokens_to_send = [{"instrument_token": t, "exchange_segment": "nse_cm"} for t in tokens]

            await run_blocking(
                self.client.subscribe,
                instrument_tokens=tokens_to_send,
                isIndex=False,
                isCashWithFO=True,
            )
            logger.info(f"✅ Subscribed to {len(tokens)} tokens")

        except Exception as e:
            logger.error(f"❌ Subscribe Failed: {e}")
            raise


market_feed = MarketFeed()
