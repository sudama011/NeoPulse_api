import asyncio
import sys
import os
import logging
from sqlalchemy import select, func

# Setup path
sys.path.append(os.getcwd())

from app.db.session import engine as db_engine
from app.models.market_data import InstrumentMaster
from app.modules.ingestion.feed import feed_engine
from app.core.events import event_bus

# Logging to see the ticks
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Streamer")

async def get_test_tokens():
    """Fetch 5 random liquid stocks from DB (e.g., RELIANCE, TCS)"""
    async with db_engine.connect() as conn:
        # We filter for large cap proxies by lot_size or just random
        query = select(InstrumentMaster.instrument_token, InstrumentMaster.trading_symbol)\
                .where(InstrumentMaster.segment == 'nse_cm')\
                .limit(5)
        
        result = await conn.execute(query)
        return result.fetchall() # Returns [(token, symbol), ...]

async def main():
    # 1. Get Tokens
    db_rows = await get_test_tokens()
    if not db_rows:
        logger.error("❌ No tokens found in DB! Did you run morning_drill.py?")
        return

    tokens = [row.instrument_token for row in db_rows]
    symbols = {str(row.instrument_token): row.trading_symbol for row in db_rows}
    
    logger.info(f"🧪 Testing Feed for: {list(symbols.values())}")

    # 2. Start Feed
    await feed_engine.start(tokens)

    # 3. Consume the Event Bus (The Consumer Pattern)
    logger.info("👀 Watching Event Bus...")
    
    while True:
        # Wait for the next tick
        tick_data = await event_bus.tick_queue.get()
        
        # Kotak usually sends a list of ticks
        if isinstance(tick_data, list):
            for tick in tick_data:
                # Parse basic fields (Check SDK docs for exact keys)
                # Usually: 'tk': token, 'ltp': last price, 'v': volume
                tk = str(tick.get('tk', ''))
                ltp = tick.get('ltp')
                
                if tk in symbols:
                    print(f"⚡ {symbols[tk]}: ₹{ltp}")
        else:
             print(f"RAW: {tick_data}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Stopped.")