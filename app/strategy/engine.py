import asyncio
import logging
from typing import Dict, List

from sqlalchemy import select

from app.data.stream import data_stream
from app.db.session import AsyncSessionLocal
from app.models.config import SystemConfig
from app.strategy import get_strategy_class, list_strategies
from app.strategy.base import BaseStrategy

logger = logging.getLogger("StrategyEngine")


class StrategyEngine:
    """
    The Orchestrator.
    - Loads strategies from DB using the Strategy Registry.
    - Runs each strategy in an isolated Async Task.
    - Handles graceful shutdown.
    """

    def __init__(self):
        self.active_strategies: Dict[str, BaseStrategy] = {}
        self.tasks: List[asyncio.Task] = []
        self._running = False

    async def initialize(self):
        """
        Load strategies from database configuration using the registry.
        """
        logger.info(f"🧠 Strategy Engine: Initializing... Available strategies: {list_strategies()}")

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(SystemConfig).where(SystemConfig.key == "strategy_config"))
            config = result.scalars().first()

            if config and config.symbols:
                # Expected JSON in DB:
                # {"SYMBOLS": [{"name": "RELIANCE", "token": "738561", "params": {"ema_period": 200}}]}
                strategy_type = config.strategy_name or "MACD_VOLUME"
                symbols_config = config.symbols.get("SYMBOLS", [])

                for s in symbols_config:
                    try:
                        # Use registry to create strategy
                        st_type = s.get("strategy_type", strategy_type)
                        strategy_cls = get_strategy_class(st_type)
                        strategy = strategy_cls(
                            name=f"{st_type}_{s['name']}",
                            symbol=s["name"],
                            token=s["token"],
                            params=s.get("params", {}),
                        )
                        self.add_strategy(strategy)
                    except ValueError as e:
                        logger.error(f"❌ {e}")
            else:
                logger.warning("⚠️ No Strategy Config found in DB. Engine Idle.")

    def add_strategy(self, strategy: BaseStrategy):
        self.active_strategies[strategy.token] = strategy
        logger.info(f"🧩 Registered Strategy: {strategy.name} [{strategy.token}]")

    async def _run_strategy_loop(self, strategy: BaseStrategy):
        """
        The isolated heartbeat for a single strategy.
        """
        logger.info(f"🏁 Starting Loop: {strategy.name}")

        # 1. Initialize (Sync Position)
        await strategy.initialize()

        # 2. Subscribe to Data Stream
        try:
            # Use Context Manager to ensure clean unsubscribe
            async with await data_stream.subscribe(strategy.token) as subscription:
                while self._running and strategy.is_active:
                    try:
                        # Wait for tick
                        tick = await subscription.get()

                        # Execute Logic (Protected by safe_on_tick)
                        await strategy.safe_on_tick(tick)

                    except asyncio.CancelledError:
                        logger.info(f"🛑 Task Cancelled: {strategy.name}")
                        break
                    except Exception as e:
                        logger.error(f"💥 Critical Loop Error in {strategy.name}: {e}")
                        await asyncio.sleep(1)  # Prevent tight loop on error
        finally:
            logger.info(f"👋 Strategy Loop Ended: {strategy.name}")

    async def start(self):
        """
        Ignition.
        """
        if self._running:
            return

        self._running = True
        logger.info(f"🚀 Strategy Engine: Launching {len(self.active_strategies)} Strategies...")

        # Spawn tasks
        for token, strategy in self.active_strategies.items():
            task = asyncio.create_task(self._run_strategy_loop(strategy))
            self.tasks.append(task)

    async def stop(self):
        """
        Graceful Shutdown.
        """
        logger.info("🛑 Strategy Engine: Stopping...")
        self._running = False

        # Cancel all tasks
        for task in self.tasks:
            task.cancel()

        # Wait for cleanup
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

        self.tasks = []
        logger.info("✅ Strategy Engine: Stopped.")


strategy_engine = StrategyEngine()
