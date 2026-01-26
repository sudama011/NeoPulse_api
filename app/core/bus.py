# app/core/events.py
import asyncio
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class EventBus:
    """
    Thread-Safe Async Event Bus.

    Decouples the SDK's socket thread from async Strategy Engine.
    Uses bounded queues with backpressure to prevent memory explosion.

    Design:
    - tick_queue: HIGH-THROUGHPUT, lossy (older ticks dropped if full)
    - order_queue: CRITICAL, blocking (waits for space)
    """

    _instance = None

    # Queue configuration
    TICK_QUEUE_SIZE = 1000  # Max 1000 pending ticks
    ORDER_QUEUE_SIZE = 100  # Max 100 pending orders

    def __init__(self):
        # HIGH-THROUGHPUT TICK QUEUE: Lossy on overflow
        # If full, old ticks are dropped (acceptable for market data)
        self.tick_queue: asyncio.Queue = asyncio.Queue(maxsize=self.TICK_QUEUE_SIZE)

        # CRITICAL ORDER QUEUE: Blocking on overflow
        # If full, subscription blocks (never drop order updates)
        self.order_queue: asyncio.Queue = asyncio.Queue(maxsize=self.ORDER_QUEUE_SIZE)

        # Stats for monitoring
        self.ticks_dropped = 0
        self.orders_queued = 0

    @classmethod
    def get_instance(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = EventBus()
        return cls._instance

    async def put_tick(self, tick: Dict[str, Any]) -> None:
        """
        Queue a tick with lossy backpressure.

        If queue is full, drops the oldest tick to make room.
        This is acceptable because newer tick data is more relevant.

        Args:
            tick: Tick data dict
        """
        try:
            # Try non-blocking put
            self.tick_queue.put_nowait(tick)
        except asyncio.QueueFull:
            # Queue full: drop oldest tick and add new one
            try:
                old_tick = self.tick_queue.get_nowait()
                self.ticks_dropped += 1

                if self.ticks_dropped % 100 == 0:
                    logger.warning(
                        f"⚠️ Tick queue saturated: {self.ticks_dropped} ticks dropped. "
                        f"Strategy is slower than market data rate."
                    )

                # Add new tick
                self.tick_queue.put_nowait(tick)
            except asyncio.QueueEmpty:
                pass

    async def put_order(self, order_data: Dict[str, Any]) -> None:
        """
        Queue an order update with blocking backpressure.

        If queue is full, waits for space (order updates are critical).

        Args:
            order_data: Order update dict
        """
        try:
            # Blocking put: wait for space
            await asyncio.wait_for(self.order_queue.put(order_data), timeout=5.0)
            self.orders_queued += 1
        except asyncio.TimeoutError:
            logger.error(
                "❌ Order queue timeout: couldn't queue order update within 5s. " "Order processor may be hung."
            )

    def get_stats(self) -> Dict[str, Any]:
        """Returns queue statistics for monitoring."""
        return {
            "tick_queue_size": self.tick_queue.qsize(),
            "tick_queue_max": self.TICK_QUEUE_SIZE,
            "tick_queue_usage": f"{self.tick_queue.qsize() / self.TICK_QUEUE_SIZE * 100:.1f}%",
            "ticks_dropped": self.ticks_dropped,
            "order_queue_size": self.order_queue.qsize(),
            "order_queue_max": self.ORDER_QUEUE_SIZE,
            "orders_queued": self.orders_queued,
        }


# Global Singleton
event_bus = EventBus.get_instance()
