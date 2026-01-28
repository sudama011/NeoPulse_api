import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List

logger = logging.getLogger("DataStream")


class DataStream:
    """
    High-Performance Event Bus with Pub/Sub cleanup and Lag Monitoring.
    """

    def __init__(self):
        # Main Ingestion Queue
        self.tick_queue = asyncio.Queue(maxsize=10000)

        # Subscribers: {token: {subscriber_id: Queue}}
        self._subscribers: Dict[str, Dict[str, asyncio.Queue]] = {}

        # Monitoring
        self.metrics = {"in_rate": 0, "dropped_main": 0, "dropped_sub": 0}
        self.last_log_time = time.time()

    async def publish(self, ticks: List[Dict[str, Any]]):
        """
        Ingests a batch of ticks. Non-blocking.
        """
        if not ticks:
            return

        try:
            self.tick_queue.put_nowait(ticks)
            self.metrics["in_rate"] += len(ticks)
        except asyncio.QueueFull:
            self.metrics["dropped_main"] += len(ticks)
            # Log periodically to avoid spamming I/O
            if time.time() - self.last_log_time > 5:
                logger.warning("⚠️ SYSTEM OVERLOAD: Main Tick Queue Full. Dropping packets.")
                self.last_log_time = time.time()

    async def subscribe(self, token: str) -> "Subscription":
        """
        Returns a Subscription handle that auto-cleans up on exit.
        """
        # Buffer size per strategy (allow some burstiness)
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

                    if token in self._subscribers:
                        # Clone list to safe iterate while modifying
                        for sub_id, q in list(self._subscribers[token].items()):
                            try:
                                q.put_nowait(tick)
                            except asyncio.QueueFull:
                                self.metrics["dropped_sub"] += 1
                                # Log specifically which consumer is slow?
                                # For now, just global metric to keep router fast.

            except Exception as e:
                logger.error(f"❌ Router Error: {e}")
                await asyncio.sleep(0.1)  # Prevent CPU spin loop on error


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
