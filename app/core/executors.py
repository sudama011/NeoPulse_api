import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable

logger = logging.getLogger("Concurrency")


class GlobalExecutor:
    def __init__(self):
        self._pool: ThreadPoolExecutor | None = None

    def start(self):
        """Initialize the thread pool."""
        if self._pool is None:
            # max_workers=20 allows for more concurrent broker calls (network I/O)
            self._pool = ThreadPoolExecutor(max_workers=20, thread_name_prefix="BrokerWorker")
            logger.info("🚀 Global Thread Pool Started")

    def stop(self):
        """Gracefully shutdown."""
        if self._pool:
            logger.info("🛑 Shutting down Global Thread Pool...")
            self._pool.shutdown(wait=True)
            self._pool = None
            logger.info("✅ Thread Pool Stopped")

    def get_pool(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self.start()  # Lazy load if needed
        return self._pool


# Global Instance
global_executor = GlobalExecutor()


async def run_blocking(func: Callable, *args, **kwargs) -> Any:
    """
    Executes a blocking function in the global thread pool.
    """
    loop = asyncio.get_running_loop()
    func_part = partial(func, *args, **kwargs)
    return await loop.run_in_executor(global_executor.get_pool(), func_part)
