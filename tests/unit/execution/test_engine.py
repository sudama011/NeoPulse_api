from unittest.mock import AsyncMock, patch

import pytest

from app.execution.engine import ExecutionEngine
from app.schemas.execution import OrderStatus


@pytest.mark.asyncio
async def test_iceberg_partial_failure():
    engine = ExecutionEngine()

    # Mock Risk Manager (Allow trade)
    engine.risk_manager.can_trade = AsyncMock(return_value=True)
    engine.master_data.get_data = lambda s: {"freeze_qty": 100}

    # Mock Broker: First 2 calls succeed, 3rd fails
    # We want to buy 300 total (3 legs of 100)

    # Success Response
    ok_resp = {"stat": "Ok", "nOrdNo": "1001"}
    # Fail Response
    fail_resp = {"stat": "Not_Ok", "errMsg": "Network Error"}

    engine.broker.place_order = AsyncMock(side_effect=[ok_resp, ok_resp, fail_resp])

    # Execute
    result = await engine.execute_order("REL", "123", "BUY", 300)

    # Assertions
    assert result.status == OrderStatus.PARTIAL
    assert result.filled_qty == 200  # 2 legs succeeded
    assert "Network Error" in result.error_message
    assert "1001" in result.order_id
