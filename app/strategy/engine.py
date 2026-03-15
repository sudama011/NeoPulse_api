import asyncio
import logging
from typing import Dict, List

from sqlalchemy import select

from app.data.stream import data_stream
from app.db.session import AsyncSessionLocal
from app.models.strategy import StrategyInstance
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
        Load active strategy instances from database using the registry.
        """
        logger.info(f"🧠 Strategy Engine: Initializing... Available strategies: {list_strategies()}")

        async with AsyncSessionLocal() as session:
            # Load all active strategy instances
            result = await session.execute(select(StrategyInstance).where(StrategyInstance.is_active == True))
            instances = result.scalars().all()

            if instances:
                for inst in instances:
                    try:
                        # Use registry to create strategy
                        strategy_cls = get_strategy_class(inst.strategy_type)
                        strategy = strategy_cls(
                            name=inst.instance_name,
                            symbol=inst.symbol,
                            token=inst.token,
                            params={},
                            leverage=inst.leverage,
                        )
                        self.add_strategy(strategy)
                        logger.info(
                            f"✅ Loaded: {inst.instance_name} ({inst.strategy_type} on {inst.symbol}, "
                            f"leverage={inst.leverage}x, sizing={inst.sizing_method})"
                        )
                    except ValueError as e:
                        logger.error(f"❌ Failed to load {inst.instance_name}: {e}")
            else:
                logger.warning("⚠️ No active strategy instances found in DB. Engine Idle.")

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
        for strategy in self.active_strategies.values():
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
