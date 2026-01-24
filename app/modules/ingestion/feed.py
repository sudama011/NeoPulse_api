# app/modules/ingestion/feed.py
import asyncio
import logging
import json
from app.adapters.kotak.client import kotak_client
from app.core.events import event_bus
from app.core.settings import settings

logger = logging.getLogger("LiveFeed")

class FeedEngine:
    def __init__(self):
        self.client = kotak_client.client
        self.is_running = False
        self._loop = None # Reference to the main asyncio loop

    def on_tick(self, message):
        """
        ⚠️ CRITICAL: This runs in a separate SDK Thread.
        Do NOT await anything here. 
        Do NOT touch the DB here.
        Just push to the bridge.
        """
        try:
            # message is usually a List or Dict from the SDK
            # We schedule the 'put' operation on the main event loop
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(
                    event_bus.tick_queue.put_nowait,
                    message
                )
        except Exception as e:
            logger.error(f"🔥 Bridging Error: {e}")

    def on_error(self, error):
        logger.error(f"❌ Socket Error: {error}")

    def on_close(self, message):
        logger.warning(f"⚠️ Socket Closed: {message}")

    async def start(self, tokens: list):
        """
        Starts the WebSocket and subscribes to tokens.
        """
        self._loop = asyncio.get_running_loop()
        
        # 1. Login if needed
        kotak_client.login()
        
        # 2. Attach Callbacks to the SDK Wrapper
        # The SDK uses these attributes to route messages
        self.client.on_message = self.on_tick
        self.client.on_error = self.on_error
        self.client.on_close = self.on_close
        self.client.on_open = lambda msg: logger.info("✅ WebSocket Connected!")

        # 3. Subscribe (SnapQuote=False for Full Ticks)
        logger.info(f"📡 Subscribing to {len(tokens)} tokens...")
        
        # Determine the format based on SDK version (List of dicts or just tokens)
        # Standard Kotak Neo V2 format:
        subscription_packet = [
            {"instrument_token": str(t), "exchange_segment": "nse_cm"} 
            for t in tokens
        ]
        
        kotak_client.subscribe(
            tokens=subscription_packet, 
            is_index=False, 
            is_depth=False
        )
        self.is_running = True

feed_engine = FeedEngine()