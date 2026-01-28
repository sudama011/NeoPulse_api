import pytest
from unittest.mock import AsyncMock, patch
from app.strategy.base import BaseStrategy
from app.schemas.execution import OrderResponse, OrderStatus

# Concrete implementation for testing
class TestStrategy(BaseStrategy):
    async def on_tick(self, tick):
        pass

@pytest.fixture
def strategy():
    return TestStrategy(name="TEST", symbol="INFY", token="123")

@pytest.mark.asyncio
async def test_sync_position(strategy):
    mock_resp = {
        "data": [{"instrumentToken": "123", "netQty": "50", "avgPrice": "1500.0"}]
    }
    
    with patch("app.strategy.base.kotak_adapter") as mock_adapter:
        mock_adapter.is_logged_in = True
        mock_adapter.get_positions = AsyncMock(return_value=mock_resp)
        
        await strategy.sync_position()
        
        assert strategy.position == 50
        assert strategy.avg_price == 1500.0

@pytest.mark.asyncio
async def test_buy_entry_checks_risk(strategy):
    # Scenario: Long Entry (Pos 0 -> 10)
    strategy.position = 0
    
    with patch("app.strategy.base.risk_manager") as mock_risk, \
         patch("app.strategy.base.execution_engine") as mock_exec:
         
        # Make calculate_size and can_trade AsyncMocks explicitly
        mock_risk.calculate_size = AsyncMock(return_value=10)
        mock_risk.can_trade = AsyncMock(return_value=True)
        
        mock_exec.execute_order = AsyncMock(return_value=OrderResponse(
            order_id="1", status=OrderStatus.COMPLETE, filled_qty=10
        ))
        
        await strategy.buy(price=100.0)
        
        # Verify call
        mock_risk.can_trade.assert_awaited() 
        assert strategy.position == 10

@pytest.mark.asyncio
async def test_sell_exit_skips_risk_check(strategy):
    # Scenario: Long Exit (Pos 50 -> 0)
    strategy.position = 50
    
    with patch("app.strategy.base.risk_manager") as mock_risk, \
         patch("app.strategy.base.execution_engine") as mock_exec:
         
        mock_exec.execute_order = AsyncMock(return_value=OrderResponse(
            order_id="2", status=OrderStatus.COMPLETE, filled_qty=50
        ))
        
        # Ensure the method is a mock we can inspect
        mock_risk.can_trade = AsyncMock() 

        # Sell all
        await strategy.sell(price=100.0)
        
        # FIX: Use assert_not_called() instead of assert_not_awaited()
        # Since we didn't await it, we effectively didn't call it.
        mock_risk.can_trade.assert_not_called()
        assert strategy.position == 0