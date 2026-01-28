import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.executors import global_executor
from app.core.logger import setup_logging
from app.data.feed import market_feed
from app.execution.engine import execution_engine
from app.risk.manager import risk_manager
from app.strategy.engine import strategy_engine

setup_logging()
logger = logging.getLogger("API")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application Lifecycle Management.
    """
    # --- STARTUP ---
    logger.info("🌐 NeoPulse API Starting...")

    # 1. Start Thread Pool (Infrastructure)
    global_executor.start()

    # 2. Initialize Execution (Connect to Broker)
    # This ensures we have a valid session before strategies try to sync positions
    await execution_engine.initialize()

    # 3. Initialize Risk System (Sync PnL, Load Config)
    await risk_manager.initialize()

    # 4. Start Data Feed (Background Task)
    # The feed runs forever, reconnecting automatically
    feed_task = asyncio.create_task(market_feed.connect())

    # 5. Initialize Strategy Engine (Load Strategies from DB but don't start trading yet)
    await strategy_engine.initialize()

    logger.info("✅ System Ready. Waiting for Start Signal via API.")

    yield  # API requests handled here

    # --- SHUTDOWN ---
    logger.info("🛑 API Stopping... Initiating graceful shutdown.")

    try:
        # 1. Stop Strategies
        await strategy_engine.stop()

        # 2. Stop Feed (Cancel the task)
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass

        # 3. Close DB / ThreadPool
        from app.db.session import engine

        await engine.dispose()
        global_executor.stop()

    except Exception as e:
        logger.error(f"❌ Error during shutdown: {e}")
    finally:
        logger.info("👋 Shutdown Complete.")


app = FastAPI(
    title="NeoPulse", description="High-Frequency Algorithmic Trading Platform", version="2.0.0", lifespan=lifespan
)

app.include_router(api_router, prefix="/api/v1")

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
