import logging
import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.api.v1.router import api_router
from app.core.logger import setup_logging
from app.utils.scheduler import MarketScheduler

# Setup Logging
setup_logging()
logger = logging.getLogger("API")

scheduler = MarketScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the Smart Scheduler (NOT the strategy directly)
    task = asyncio.create_task(scheduler.run_loop())
    yield
    # Cleanup
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    
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