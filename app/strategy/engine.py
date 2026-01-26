import asyncio
import logging
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.market_data import InstrumentMaster
from app.core.bus import event_bus
from app.risk.manager import risk_manager
from app.execution.engine import execution_engine

from app.strategy.strategies import (
    MomentumStrategy,
    ORBStrategy,
    MeanReversionStrategy,
    RuleBasedStrategy,
)
from app.strategy.generic import GenericStrategy

logger = logging.getLogger("StrategyEngine")


class StrategyEngine:
    def __init__(self):
        self.strategies = {}  # {token: StrategyInstance}
        self.is_running = False

    async def start(self, config: dict):
        """Starts the engine with a configuration."""
        if self.is_running:
            return

        target_symbols = config.get("symbols", [])
        strat_name = config.get("strategy", "MOMENTUM")

        # 1. Resolve Tokens
        token_map = await self._resolve_tokens(target_symbols)

        # 2. Initialize Strategies
        for symbol, token in token_map.items():
            if strat_name == "GENERIC":
                s = GenericStrategy(symbol, token, config.get("rules", {}))
            elif strat_name == "MOMENTUM":
                s = MomentumStrategy(symbol, token)
            elif strat_name == "ORB":
                s = ORBStrategy(symbol, token)
            elif strat_name == "MEAN_REVERSION":
                s = MeanReversionStrategy(symbol, token)
            elif strat_name == "RULE_ENGINE":
                s = RuleBasedStrategy(symbol, token, config.get("rules", {}))
            else:
                logger.error(f"Unknown Strategy: {strat_name}")
                continue

            # Enable features from config
            s.trailing_enabled = config.get("trailing_sl", False)
            self.strategies[token] = s

        self.is_running = True
        logger.info(f"🚀 Engine Started with {len(self.strategies)} active strategies")

        # 3. Start Event Loop
        asyncio.create_task(self._tick_loop())

    async def stop(self):
        """Stops engine and optionally squares off."""
        self.is_running = False
        await self._square_off_all()
        self.strategies.clear()
        logger.info("🛑 Engine Stopped")

    async def _tick_loop(self):
        """Consumes ticks from EventBus."""
        while self.is_running:
            try:
                payload = await event_bus.tick_queue.get()
                ticks = (
                    payload.get("data", []) if isinstance(payload, dict) else payload
                )

                for tick in ticks:
                    token = str(tick.get("tk"))
                    if token in self.strategies:
                        # Fire and forget strategy logic
                        asyncio.create_task(self.strategies[token].on_tick(tick))
            except Exception as e:
                logger.error(f"Loop Error: {e}")

    async def _square_off_all(self):
        logger.warning("🟥 Squaring off all positions...")
        for s in self.strategies.values():
            if s.position != 0:
                side = "SELL" if s.position > 0 else "BUY"
                await execution_engine.execute_order(
                    s.symbol, s.token, side, abs(s.position)
                )

    async def _resolve_tokens(self, symbols):
        token_map = {}
        async with AsyncSessionLocal() as session:
            stmt = select(
                InstrumentMaster.trading_symbol, InstrumentMaster.instrument_token
            ).where(InstrumentMaster.trading_symbol.in_(symbols))
            result = await session.execute(stmt)
            for row in result.fetchall():
                token_map[row[0]] = str(row[1])
        return token_map


# Global Accessor
strategy_engine = StrategyEngine()
