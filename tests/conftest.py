import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

# 1. Force AsyncIO loop for pytest-asyncio
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

# 2. Mock the Broker Adapter
@pytest.fixture
def mock_broker():
    """
    Replaces the real KotakAdapter with a Mock.
    """
    mock = MagicMock()
    mock.is_logged_in = True
    
    # Async methods must return Awaitables
    mock.place_order = AsyncMock(return_value={"stat": "Ok", "nOrdNo": "123456"})
    mock.get_positions = AsyncMock(return_value={"data": []})
    mock.get_limits = AsyncMock(return_value={"net": "100000"})
    
    return mock

# 3. Mock the Database Session
@pytest.fixture
def mock_db_session():
    session = AsyncMock()
    return session