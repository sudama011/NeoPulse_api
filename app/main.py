import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.logger import setup_logging
from app.execution.engine import execution_engine
from app.risk.manager import risk_manager
from app.strategy.engine import strategy_engine
from app.data.engine import data_engine

# Setup Logging
setup_logging()
logger = logging.getLogger("API")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application Lifecycle Management.

    Startup: Restore bot state from DB
    Shutdown: Gracefully close all positions with timeout
    """
    # --- STARTUP ---
    logger.info("🌐 NeoPulse API Starting...")
    
    # 1. Start Execution (Connects Broker)
    await execution_engine.initialize()
    
    # 2. Start Data (Loads Tokens, Connects Socket)
    await data_engine.initialize() 
    
    # 3. Start Risk
    await risk_manager.initialize()

    logger.info("🛑 Bot is currently STOPPED. Use POST /api/v1/engine/start to launch.")

    yield  # Application runs here

    # --- SHUTDOWN (Graceful) ---
    logger.info("🛑 API Stopping... Initiating graceful shutdown.")

    try:
        # Set timeout for graceful shutdown
        async with asyncio.timeout(10):  # 10 second timeout
            if strategy_engine.is_running:
                strategy_engine.is_running = False
                logger.info("📊 Closing all open positions...")
                await strategy_engine.square_off_all()

                # Wait for orders to be processed
                await asyncio.sleep(1)

            # Close DB connections
            from app.db.session import engine

            await engine.dispose()

    except asyncio.TimeoutError:
        logger.critical("❌ Shutdown timeout exceeded! Forcing termination.")
    except Exception as e:
        logger.error(f"❌ Error during shutdown: {e}")
    finally:
        logger.info("✅ Shutdown Complete.")


# Initialize App
app = FastAPI(title="NeoPulse", description="Algorithmic Trading Control Plane", version="1.0.0", lifespan=lifespan)

# Include the V1 Router
app.include_router(api_router, prefix="/api/v1")

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
