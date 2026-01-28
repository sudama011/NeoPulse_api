import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.executors import global_executor
from app.core.logger import setup_logging
from app.data.feed import market_feed

# 1. Import master_data
from app.data.master import master_data
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

    # 2. Load Master Data
    await master_data.initialize()

    # 3. Initialize Execution (Connect to Broker)
    await execution_engine.initialize()

    # 4. Initialize Risk System
    await risk_manager.initialize()

    # 5. Start Data Feed (Background Task)
    feed_task = asyncio.create_task(market_feed.connect())

    # 6. Initialize Strategy Engine
    await strategy_engine.initialize()

    logger.info("✅ System Ready. Waiting for Start Signal via API.")

    yield  # API requests handled here

    # --- SHUTDOWN ---
    logger.info("🛑 API Stopping... Initiating graceful shutdown.")

    try:
        # 1. Stop Strategies
        await strategy_engine.stop()

        # 2. Stop Feed
        market_feed._stop_event.set()  # Signal stop
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

# Enable CORS (Good practice for local dashboards/Postman)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
