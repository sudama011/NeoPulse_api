from unittest.mock import AsyncMock, patch

import pytest

from app.risk.sentinel import RiskSentinel
from app.schemas.common import RiskConfig


@pytest.mark.asyncio
async def test_sentinel_sync_recovery():
    config = RiskConfig(max_daily_loss=1000.0, max_capital_per_trade=50000.0, max_open_trades=5)
    sentinel = RiskSentinel(config)

    # Mock Kotak Adapter Response
    # Scenario: We made ₹500 on one trade, lost ₹200 on another. Net +300.
    # One position is still open.
    mock_response = {
        "data": [{"realizedPNL": "500.00", "netQty": "0"}, {"realizedPNL": "-200.00", "netQty": "50"}]  # Closed  # Open
    }

    with patch("app.risk.sentinel.kotak_adapter") as mock_adapter:
        mock_adapter.is_logged_in = True
        mock_adapter.get_positions = AsyncMock(return_value=mock_response)

        await sentinel.sync_state()

        # Assertions
        assert sentinel.current_pnl == 300.0
        assert sentinel.open_trades == 1
        assert sentinel.config.kill_switch_active is False


@pytest.mark.asyncio
async def test_sentinel_kill_switch_on_startup():
    config = RiskConfig(max_daily_loss=1000.0, max_capital_per_trade=50000.0)
    sentinel = RiskSentinel(config)

    # Scenario: Huge loss previously
    mock_response = {"data": [{"realizedPNL": "-1500.00", "netQty": "0"}]}

    with patch("app.risk.sentinel.kotak_adapter") as mock_adapter:
        mock_adapter.is_logged_in = True
        mock_adapter.get_positions = AsyncMock(return_value=mock_response)

        await sentinel.sync_state()

        assert sentinel.current_pnl == -1500.0
        assert sentinel.config.kill_switch_active is True
