import asyncio
import sys
import os
import logging
from sqlalchemy import select

# 1. Fix Path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.insert(0, project_root)

# 2. Setup Logger
from app.core.logger import setup_logging
setup_logging()

# Imports
from app.services.master_data.feed import feed_engine
from app.services.strategy.manager import strategy_engine
from app.services.strategy.lib.momentum import MomentumStrategy
from app.db.session import engine as db_engine
from app.models.market_data import InstrumentMaster

logger = logging.getLogger("MainRunner")

async def get_target_tokens():
    """Fetch top 2 liquid stocks to test our strategy"""
    async with db_engine.connect() as conn:
        # We pick NIFTY 50 stocks (usually large caps) or just limit 2
        query = select(InstrumentMaster.instrument_token, InstrumentMaster.trading_symbol)\
                .where(InstrumentMaster.segment == 'nse_cm')\
                .limit(2)
        result = await conn.execute(query)
        return result.fetchall()

async def main():
    # A. Fetch Target Stocks
    rows = await get_target_tokens()
    if not rows:
        logger.error("❌ No symbols found in DB. Did you run 'morning_drill.py'?")
        return

    tokens_to_subscribe = []
    
    # B. Register Strategies
    logger.info("🧠 Initializing Strategies...")
    for row in rows:
        token = str(row.instrument_token)
        symbol = row.trading_symbol
        tokens_to_subscribe.append(token)
        
        # Attach the 'MomentumStrategy' to this stock
        strategy_engine.add_strategy(
            strategy_class=MomentumStrategy,
            symbol=symbol,
            token=token
        )

    # C. Start the WebSocket Feed
    # This will start pushing ticks into the EventBus
    await feed_engine.start(tokens_to_subscribe)

    # D. Start the Strategy Engine
    # This will consume ticks and execute logic
    await strategy_engine.start()

if __name__ == "__main__":
    # Windows Event Loop Policy fix
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down Bot...")