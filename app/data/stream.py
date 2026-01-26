import asyncio
import logging
from typing import Any, Dict, List

logger = logging.getLogger("DataStream")


class DataStream:
    """
    High-Performance Event Bus for Market Data.
    Uses asyncio.Queue for non-blocking distribution.
    """

    def __init__(self):
        # A Queue for incoming ticks
        self.tick_queue = asyncio.Queue(maxsize=10000)

        # Subscribers (Strategies listening to specific tokens)
        # Structure: { 'token_123': [queue_1, queue_2] }
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}

        # Global broadcast channels (e.g., for logging or UI)
        self._global_listeners: List[asyncio.Queue] = []

    async def publish(self, ticks: List[Dict[str, Any]]):
        """
        Ingests a batch of ticks and routes them to subscribers.
        """
        if not ticks:
            return

        # 1. Put into main processing queue (decouples socket from processing)
        try:
            self.tick_queue.put_nowait(ticks)
        except asyncio.QueueFull:
            logger.warning("⚠️ Tick Queue Full! Dropping Market Data packets.")

    async def subscribe(self, token: str) -> asyncio.Queue:
        """
        Returns a Queue that will receive ticks for this token.
        """
        q = asyncio.Queue()
        if token not in self._subscribers:
            self._subscribers[token] = []
        self._subscribers[token].append(q)
        return q

    async def consume(self):
        """
        Background Worker: Routes ticks from Main Queue to Subscribers.
        """
        while True:
            ticks = await self.tick_queue.get()

            for tick in ticks:
                token = str(tick.get("tk"))  # 'tk' is Kotak's token key

                # Route to specific subscribers
                if token in self._subscribers:
                    for q in self._subscribers[token]:
                        if not q.full():
                            q.put_nowait(tick)

                # Route to global listeners
                for q in self._global_listeners:
                    if not q.full():
                        q.put_nowait(tick)


# Global Accessor
data_stream = DataStream()
