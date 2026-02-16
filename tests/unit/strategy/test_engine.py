from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.strategy.base import BaseStrategy
from app.strategy.engine import StrategyEngine
from app.strategy.strategies import MACDVolumeStrategy


# A dummy strategy for testing
class MockStrategy(BaseStrategy):
    async def on_tick(self, tick):
        pass


@pytest.mark.asyncio
async def test_engine_initialization():
    engine = StrategyEngine()

    # Mock DB Session
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_config = MagicMock()

    # Setup DB Return
    mock_config.symbols = {"SYMBOLS": [{"name": "REL", "token": "123", "params": {}}]}
    mock_result.scalars.return_value.first.return_value = mock_config
    mock_session.execute.return_value = mock_result

    with patch("app.strategy.engine.AsyncSessionLocal", return_value=mock_session):
        await engine.initialize()

        assert len(engine.active_strategies) == 1
        assert "123" in engine.active_strategies
        assert isinstance(engine.active_strategies["123"], MACDVolumeStrategy)


@pytest.mark.asyncio
async def test_engine_lifecycle():
    engine = StrategyEngine()

    # Mock a strategy
    mock_strat = AsyncMock()
    mock_strat.token = "123"
    mock_strat.is_active = True
    engine.add_strategy(mock_strat)

    # Mock Data Stream
    mock_sub = AsyncMock()
    mock_sub.get = AsyncMock(return_value={"ltp": 100})

    with patch("app.strategy.engine.data_stream.subscribe", return_value=mock_sub):
        # Start
        await engine.start()
        assert engine._running is True
        assert len(engine.tasks) == 1

        # Stop
        await engine.stop()
        assert engine._running is False
        assert len(engine.tasks) == 0


@pytest.mark.asyncio
async def test_strategy_buy_order(mock_broker):
    """Test that calling buy() sends correct params to Broker."""

    strategy = MockStrategy(name="TestStrat", symbol="RELIANCE", token="2885")

    # We patch the 'execution_engine.broker' to be our mock
    with (
        patch("app.execution.engine.execution_engine.broker", mock_broker),
        patch("app.execution.engine.execution_engine.risk_manager") as mock_risk,
    ):

        # Bypass risk check
        mock_risk.can_trade = AsyncMock(return_value=True)

        # EXECUTE
        await strategy.buy(price=2500.0, qty=10, tag="TEST_ENTRY")

        # VERIFY
        mock_broker.place_order.assert_called_once()
        call_args = mock_broker.place_order.call_args[0][0]

        assert call_args["transaction_type"] == "B"
        assert call_args["quantity"] == 10
        assert call_args["price"] == 2500.0
        assert call_args["trading_symbol"] == "RELIANCE"

        print("✅ Buy Order successfully routed to Broker Adapter")
