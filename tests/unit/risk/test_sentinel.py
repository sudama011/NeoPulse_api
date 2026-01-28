import pytest
from unittest.mock import AsyncMock, patch
from app.risk.sentinel import RiskSentinel
from app.schemas.common import RiskConfig

@pytest.fixture
def sentinel():
    config = RiskConfig(
        max_daily_loss=1000.0, 
        max_concurrent_trades=2,
        kill_switch_active=False
    )
    return RiskSentinel(config)

@pytest.mark.asyncio
async def test_kill_switch_activation(sentinel):
    """Test if Kill Switch triggers when Net PnL hits limit."""
    
    # Scenario: Loss is -950, but Taxes are -60. Total = -1010.
    # This should TRIGGER the kill switch (Limit is 1000).
    
    # Mock Broker Response
    mock_positions = {
        "data": [
            {
                "instrumentToken": "111", 
                "realizedPNL": "-950.0",   # Gross PnL
                "buyAmt": "50000",         # Turnover for Tax Calc
                "sellAmt": "50000"
            }
        ]
    }
    
    with patch("app.risk.sentinel.kotak_adapter") as mock_adapter:
        mock_adapter.is_logged_in = True
        mock_adapter.get_positions = AsyncMock(return_value=mock_positions)
        
        # Run Sync
        await sentinel.sync_state()
        
        # Verify Logic
        assert sentinel.gross_pnl == -950.0
        # Turnover = 100k. Tax approx 0.05% = ~50. 
        assert sentinel.net_pnl < -950.0 
        
        # CRITICAL CHECK
        assert sentinel.config.kill_switch_active is True
        print(f"✅ Kill Switch Activated at Net PnL: {sentinel.net_pnl}")

@pytest.mark.asyncio
async def test_concurrency_limit(sentinel):
    """Test that we cannot exceed max open trades."""
    sentinel.open_trades = 2
    sentinel.config.max_concurrent_trades = 2
    
    allowed = await sentinel.check_pre_trade("RELIANCE", 10, 25000)
    assert allowed is False