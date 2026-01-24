import logging
import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.api.v1.router import api_router
from app.modules.strategy.engine import strategy_engine
from app.core.logger import setup_logging

# Setup Logging
setup_logging()
logger = logging.getLogger("API")

# Lifespan Manager (Startup/Shutdown Events)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. STARTUP
    logger.info("🌐 API Starting... Launching Strategy Engine.")
    # Run the engine in the background
    task = asyncio.create_task(strategy_engine.start())
    
    yield # App runs here
    
    # 2. SHUTDOWN
    logger.info("🛑 API Stopping... Shutting down Engine.")
    strategy_engine.is_running = False
    await task

# Initialize App
app = FastAPI(
    title="NeoPulse Commander",
    description="Algorithmic Trading Control Plane",
    version="1.0.0",
    lifespan=lifespan
)

# Include the V1 Router
app.include_router(api_router, prefix="/api/v1")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)