import asyncio
import logging
import uuid
from typing import Any, Dict, List, Set

logger = logging.getLogger("DataStream")

class DataStream:
    """
    High-Performance Event Bus with Pub/Sub cleanup.
    """

    def __init__(self):
        # Main Ingestion Queue
        self.tick_queue = asyncio.Queue(maxsize=10000)
        
        # Subscribers: {token: {subscriber_id: Queue}}
        self._subscribers: Dict[str, Dict[str, asyncio.Queue]] = {}
        
        # Global listeners (e.g., Logger, UI)
        self._global_listeners: Dict[str, asyncio.Queue] = {}

    async def publish(self, ticks: List[Dict[str, Any]]):
        """
        Ingests a batch of ticks. Non-blocking.
        """
        if not ticks:
            return
        
        try:
            self.tick_queue.put_nowait(ticks)
        except asyncio.QueueFull:
            # If main queue is full, system is overloaded. Drop oldest? No, drop new (tail drop).
            logger.warning("⚠️ SYSTEM OVERLOAD: Main Tick Queue Full. Dropping packets.")

    async def subscribe(self, token: str) -> 'Subscription':
        """
        Returns a Subscription handle that auto-cleans up on exit.
        """
        queue = asyncio.Queue(maxsize=1000)
        sub_id = uuid.uuid4().hex
        
        if token not in self._subscribers:
            self._subscribers[token] = {}
        
        self._subscribers[token][sub_id] = queue
        
        return Subscription(self, token, sub_id, queue)

    def _unsubscribe(self, token: str, sub_id: str):
        if token in self._subscribers and sub_id in self._subscribers[token]:
            del self._subscribers[token][sub_id]
            if not self._subscribers[token]:
                del self._subscribers[token]
            logger.debug(f"🔌 Unsubscribed {sub_id[:8]} from {token}")

    async def consume(self):
        """
        The Heartbeat: Routes ticks to subscribers.
        """
        logger.info("⚡ DataStream Router Started")
        while True:
            try:
                ticks = await self.tick_queue.get()
                
                for tick in ticks:
                    token = str(tick.get("tk"))
                    
                    # 1. Route to Token Subscribers
                    if token in self._subscribers:
                        # Iterate over a copy to allow safe modification/deletion
                        for sub_id, q in list(self._subscribers[token].items()):
                            try:
                                q.put_nowait(tick)
                            except asyncio.QueueFull:
                                # Slow consumer detected
                                pass 

                    # 2. Route to Global Listeners
                    for sub_id, q in list(self._global_listeners.items()):
                         try:
                            q.put_nowait(tick)
                         except asyncio.QueueFull:
                            pass
                            
            except Exception as e:
                logger.error(f"❌ Router Error: {e}")

class Subscription:
    """Context Manager for safe unsubscription"""
    def __init__(self, stream: DataStream, token: str, sub_id: str, queue: asyncio.Queue):
        self.stream = stream
        self.token = token
        self.sub_id = sub_id
        self.queue = queue

    async def get(self):
        return await self.queue.get()

    def close(self):
        self.stream._unsubscribe(self.token, self.sub_id)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.close()

# Global Accessor
data_stream = DataStream()