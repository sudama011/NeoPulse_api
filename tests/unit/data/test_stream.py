import pytest
import asyncio
from app.data.stream import DataStream

@pytest.fixture
def stream():
    return DataStream()

@pytest.mark.asyncio
async def test_pub_sub_flow(stream):
    """Test that a subscriber receives published ticks."""
    
    token = "2885"
    ticks = [{"tk": token, "ltp": 2500}]
    
    # 1. Start Consumer Loop in Background
    # We use a task that we can cancel later
    consumer_task = asyncio.create_task(stream.consume())
    
    try:
        # 2. Subscribe
        sub = await stream.subscribe(token)
        
        async with sub:
            # 3. Publish
            await stream.publish(ticks)
            
            # 4. Assert Receive
            received_tick = await asyncio.wait_for(sub.get(), timeout=1.0)
            assert received_tick["tk"] == token
            assert received_tick["ltp"] == 2500
            
    finally:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

@pytest.mark.asyncio
async def test_subscription_isolation(stream):
    """Test that you don't get ticks for tokens you didn't subscribe to."""
    
    sub = await stream.subscribe("1111")
    consumer_task = asyncio.create_task(stream.consume())
    
    try:
        # Publish for a DIFFERENT token
        await stream.publish([{"tk": "9999", "ltp": 500}])
        
        # Verify queue is empty (with timeout)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sub.get(), timeout=0.1)
            
    finally:
        consumer_task.cancel()