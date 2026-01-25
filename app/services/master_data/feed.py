import asyncio
import logging
import json
from app.adapters.neo_client import neo_client
from app.core.bus import event_bus
from app.core.executors import run_blocking # <--- NEW IMPORT

logger = logging.getLogger("LiveFeed")

class FeedEngine:
    _instance = None

    def __init__(self):
        self.client = neo_client.client
        self.is_running = False
        self._loop = None
        self.active_tokens = [] 
        self.reconnect_attempts = 0

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = FeedEngine()
        return cls._instance

    def on_message_router(self, message):
        try:
            if isinstance(message, list):
                if self._loop and not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(event_bus.tick_queue.put_nowait, message)
                return

            if isinstance(message, dict):
                if 'data' in message and isinstance(message['data'], list):
                     if self._loop and not self._loop.is_closed():
                        self._loop.call_soon_threadsafe(event_bus.tick_queue.put_nowait, message['data'])
                elif 'orderId' in message or 'orderStatus' in message:
                    logger.info(f"📨 Order Update: {message.get('orderId', 'Unknown')}")
                    if self._loop and not self._loop.is_closed():
                        self._loop.call_soon_threadsafe(event_bus.order_queue.put_nowait, message)
        except Exception as e:
            logger.error(f"🔥 Routing Error: {e}")

    def on_error(self, error):
        logger.error(f"❌ Socket Error: {error}")
        self._trigger_reconnect()

    def on_close(self, message):
        logger.warning(f"⚠️ Socket Closed: {message}")
        self._trigger_reconnect()

    def _trigger_reconnect(self):
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(asyncio.create_task, self._reconnect())

    async def _reconnect(self):
        self.reconnect_attempts += 1
        wait_time = min(self.reconnect_attempts * 2, 30) 
        
        logger.info(f"🔄 Attempting Reconnect #{self.reconnect_attempts} in {wait_time}s...")
        await asyncio.sleep(wait_time)

        try:
            logger.info("🔑 Refreshing Session...")
            neo_client.is_logged_in = False 
            
            # ✅ REFACTORED: Offload blocking Login
            await run_blocking(neo_client.login)
            
            logger.info(f"📡 Re-subscribing to {len(self.active_tokens)} tokens...")
            
            self.client.on_message = self.on_message_router
            self.client.on_error = self.on_error
            self.client.on_close = self.on_close
            
            if self.active_tokens:
                sub_packet = [{"instrument_token": str(t), "exchange_segment": "nse_cm"} for t in self.active_tokens]
                # ✅ REFACTORED: Offload blocking Subscribe
                await run_blocking(neo_client.subscribe, sub_packet)
            else:
                logger.warning("⚠️ No active tokens to subscribe to.")

        except Exception as e:
            logger.error(f"💥 Reconnect Failed: {e}")

    async def start(self, tokens: list):
        self._loop = asyncio.get_running_loop()
        self.active_tokens = tokens 
        
        # ✅ REFACTORED: Login is blocking
        await run_blocking(neo_client.login)
        
        self.client.on_message = self.on_message_router
        self.client.on_error = self.on_error
        self.client.on_close = self.on_close
        self.client.on_open = lambda msg: logger.info("✅ WebSocket Connected!")

        logger.info(f"📡 Subscribing to {len(tokens)} tokens...")
        sub_packet = [{"instrument_token": str(t), "exchange_segment": "nse_cm"} for t in tokens]
        
        # ✅ REFACTORED: Subscribe is blocking
        await run_blocking(neo_client.subscribe, sub_packet)
        self.is_running = True

feed_engine = FeedEngine.get_instance()