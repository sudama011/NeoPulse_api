import pytest
import asyncio
from app.core.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState

@pytest.fixture
def cb():
    return CircuitBreaker(name="TestCB", failure_threshold=2, recovery_timeout=0.1)

@pytest.mark.asyncio
async def test_circuit_opens_on_failure(cb):
    """Test transition from CLOSED -> OPEN after failures."""
    
    async def failing_func():
        raise ValueError("Boom")

    # 1. Fail Once
    with pytest.raises(ValueError):
        await cb.call(failing_func)
    assert cb._state == CircuitState.CLOSED
    
    # 2. Fail Twice (Threshold Reached)
    with pytest.raises(ValueError):
        await cb.call(failing_func)
    
    # 3. Next call should raise CircuitOpenError immediately
    assert cb._state == CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        await cb.call(failing_func)

@pytest.mark.asyncio
async def test_circuit_recovery_half_open(cb):
    """Test transition OPEN -> HALF_OPEN -> CLOSED."""
    
    # Force OPEN state
    cb._state = CircuitState.OPEN
    cb._last_failure_time = datetime.now()
    
    # Wait for recovery timeout
    await asyncio.sleep(0.2)
    
    async def success_func():
        return "OK"
    
    # This call should trigger the PROBE (Half-Open)
    result = await cb.call(success_func)
    assert result == "OK"
    
    # Should be fully recovered now
    assert cb._state == CircuitState.CLOSED
    assert cb._failure_count == 0

from datetime import datetime