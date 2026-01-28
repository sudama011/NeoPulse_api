import asyncio
import json
import logging
import time
from typing import Optional

from app.core.executors import run_blocking
from app.core.settings import settings
from app.data.stream import data_stream
from app.execution.kotak import kotak_adapter

logger = logging.getLogger("MarketFeed")


class MarketFeed:
    """
    Self-Healing WebSocket Client with Zombie Detection & Thread-Safe Bridging.
    """

    def __init__(self):
        self.client = kotak_adapter.client
        self.subscribed_tokens = set()
        self.reconnect_delay = 2

        # Watchdog State
        self.last_packet_time = time.time()
        self.silence_threshold = 10.0
        self._stop_event = asyncio.Event()

        # 🧵 Thread Safety: We must capture the main loop to bridge calls later
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    def on_message(self, message):
        """
        Raw callback from Kotak SDK Background Thread.
        ⚠️ MUST NOT use 'await' or access 'asyncio.get_event_loop()' directly here.
        """
        self.last_packet_time = time.time()

        # 1. Normalize Data
        ticks = []
        try:
            if isinstance(message, list):
                ticks = message
            elif isinstance(message, dict) and "data" in message:
                ticks = message["data"]

            # Debug Log (Optional)
            if ticks:
                logger.info(f"📨 RAW TICK: {str(ticks[0])[:200]}...")

        except Exception:
            return

        # 2. Thread-Safe Bridge to Main Loop
        if ticks and self._main_loop and not self._main_loop.is_closed():
            # 🚀 BRIDGE: Send coroutine to the Main Loop from this thread
            asyncio.run_coroutine_threadsafe(data_stream.publish(ticks), self._main_loop)

    def on_error(self, error):
        logger.error(f"❌ Feed Error: {error}")

    def on_close(self, message):
        logger.warning(f"🔌 Feed Disconnected! Msg: {message}")

    def on_open(self, message):
        logger.info("⚡ WebSocket Connection OPENED.")

    async def connect(self):
        """
        Main Connection Loop.
        """
        # 1. Capture the Main Event Loop (We are in the main thread here)
        self._main_loop = asyncio.get_running_loop()

        self.client.on_message = self.on_message
        self.client.on_error = self.on_error
        self.client.on_close = self.on_close
        self.client.on_open = self.on_open

        logger.info("📡 Feed Manager Started.")

        while not self._stop_event.is_set():
            try:
                # Login Check
                if not settings.PAPER_TRADING:
                    if not kotak_adapter.is_logged_in:
                        logger.info("Waiting for Broker Login...")
                        await asyncio.sleep(2)
                        continue

                # Resubscribe
                if self.subscribed_tokens:
                    self.last_packet_time = time.time()
                    await self.subscribe(list(self.subscribed_tokens))

                # Watchdog Loop
                while not self._stop_event.is_set():
                    if not self.subscribed_tokens:
                        await asyncio.sleep(1)
                        self.last_packet_time = time.time()
                        continue

                    time_since_last_packet = time.time() - self.last_packet_time
                    if time_since_last_packet > self.silence_threshold:
                        raise ConnectionError(f"Zombie Connection! Silence for {time_since_last_packet:.1f}s")

                    await asyncio.sleep(1)

                self.reconnect_delay = 2

            except Exception as e:
                logger.error(f"⚠️ Feed Connection/Watchdog Error: {e}")
                logger.info(f"⏳ Retrying in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, 60)

    async def subscribe(self, tokens: list):
        if not tokens:
            return

        self.subscribed_tokens.update(tokens)
        try:
            tokens_to_send = [{"instrument_token": t, "exchange_segment": "nse_cm"} for t in tokens]

            await run_blocking(
                self.client.subscribe,
                instrument_tokens=tokens_to_send,
                isIndex=False,
            )
            logger.info(f"✅ Subscribed to {len(tokens)} tokens")
        except Exception as e:
            logger.error(f"❌ Subscribe Failed: {e}")


market_feed = MarketFeed()
