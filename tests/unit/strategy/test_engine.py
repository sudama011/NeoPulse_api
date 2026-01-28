from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.strategy.engine import StrategyEngine
from app.strategy.strategies import MACDVolumeStrategy


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
