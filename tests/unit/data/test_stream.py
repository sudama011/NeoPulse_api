import asyncio

import pytest

from app.data.stream import DataStream


@pytest.mark.asyncio
async def test_subscription_cleanup():
    stream = DataStream()
    token = "12345"

    # 1. Subscribe
    sub = await stream.subscribe(token)
    assert token in stream._subscribers
    assert len(stream._subscribers[token]) == 1

    # 2. Unsubscribe via Context Manager (simulate scope exit)
    async with sub:
        pass

    # 3. Verify Cleanup
    # Depending on implementation, the inner dict might be empty or the key removed
    if token in stream._subscribers:
        assert len(stream._subscribers[token]) == 0
    else:
        assert True
