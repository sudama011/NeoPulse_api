# app/core/events.py
import asyncio
import logging

logger = logging.getLogger(__name__)

class EventBus:
    """
    The Thread-Safe Bridge.
    Decouples the SDK's socket thread from our Async Strategy Engine.
    """
    _instance = None

    def __init__(self):
        # Unbounded queue for high-throughput ticks
        self.tick_queue = asyncio.Queue()
        
        # Queue for Order Updates (Fills, Rejections)
        self.order_queue = asyncio.Queue()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = EventBus()
        return cls._instance

# Global Singleton
event_bus = EventBus.get_instance()