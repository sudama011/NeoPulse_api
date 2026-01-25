import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable

logger = logging.getLogger("Concurrency")

# Global Broker I/O Thread Pool
# Shared across Feed, OMS, and Strategy services
broker_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="BrokerWorker")

async def run_blocking(func: Callable, *args, **kwargs) -> Any:
    """
    Executes a blocking (synchronous) function in the shared thread pool.
    Supports *args and **kwargs automatically.
    
    Usage: 
        await run_blocking(client.place_order, symbol="REL", qty=10)
    """
    loop = asyncio.get_running_loop()
    
    # We use partial to bundle args/kwargs because run_in_executor doesn't support kwargs directly
    func_part = partial(func, *args, **kwargs)
    
    return await loop.run_in_executor(broker_executor, func_part)