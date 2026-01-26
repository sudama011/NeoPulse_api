import pytest
import asyncio
import time
from app.core.executors import run_blocking, global_executor

def blocking_task(duration: float):
    time.sleep(duration)
    return "done"

@pytest.mark.asyncio
async def test_run_blocking():
    # Ensure pool is started
    global_executor.start()
    
    start = time.monotonic()
    result = await run_blocking(blocking_task, 0.1)
    end = time.monotonic()
    
    assert result == "done"
    # Should take at least 0.1s but be non-blocking to the loop conceptually
    # (Checking non-blocking requires more complex setup, but this validates execution)
    assert (end - start) >= 0.1