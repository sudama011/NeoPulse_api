import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy.future import select

from app.api.v1.router import api_router
from app.core.logger import setup_logging
from app.services.strategy.manager import strategy_engine
from app.services.risk.monitor import risk_monitor
from app.db.session import AsyncSessionLocal
from app.models.config import SystemConfig

# Setup Logging
setup_logging()
logger = logging.getLogger("API")

async def restore_state():
    """
    ♻️ CRASH RECOVERY:
    Reads the last saved configuration from the DB and restores 
    the bot's memory (Capital, Risk Limits, Strategy Params).
    """
    try:
        async with AsyncSessionLocal() as session:
            # Fetch the 'current_state' key
            result = await session.execute(select(SystemConfig).where(SystemConfig.key == "current_state"))
            config = result.scalars().first()
            
            if config:
                logger.info("♻️ Found saved state in DB. Restoring...")
                
                # 1. Restore Capital
                strategy_engine.available_capital = config.capital
                
                # 2. Restore Risk Limits
                risk_monitor.update_config(
                    max_daily_loss=config.max_daily_loss, 
                    max_concurrent_trades=config.max_concurrent_trades,
                    risk_params=config.risk_params
                )
                
                # 3. Log Restoration
                logger.info(
                    f"✅ State Restored: Strategy={config.strategy_name} | "
                    f"Capital=₹{config.capital} | "
                    f"Symbols={len(config.symbols)}"
                )
            else:
                logger.warning("⚠️ No saved state found. Bot starting with Factory Defaults.")
                
    except Exception as e:
        logger.error(f"❌ Failed to restore state: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application Lifecycle.
    """
    # --- STARTUP ---
    logger.info("🌐 NeoPulse API Starting...")
    
    # 1. Restore Memory from DB
    await restore_state()
    
    logger.info("🛑 Bot is currently STOPPED. Use POST /api/v1/engine/start to launch.")
    
    yield # Application runs here
    
    # --- SHUTDOWN ---
    logger.info("🛑 API Stopping... Killing Strategy Engine.")
    if strategy_engine.is_running:
        strategy_engine.is_running = False
        # Optional: Close DB connections or HTTP sessions here if needed
        await strategy_engine.square_off_all() 
    logger.info("✅ Shutdown Complete.")

# Initialize App
app = FastAPI(
    title="NeoPulse",
    description="Algorithmic Trading Control Plane",
    version="1.0.0",
    lifespan=lifespan
)

# Include the V1 Router
app.include_router(api_router, prefix="/api/v1")

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)