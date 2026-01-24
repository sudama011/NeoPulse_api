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

from app.db.session import engine as db_engine
from app.models.market_data import InstrumentMaster
from app.modules.ingestion.feed import feed_engine
from app.core.events import event_bus

logger = logging.getLogger("Streamer")

async def get_test_tokens():
    """Fetch 5 random tokens from DB"""
    async with db_engine.connect() as conn:
        query = select(InstrumentMaster.instrument_token, InstrumentMaster.trading_symbol)\
                .where(InstrumentMaster.segment == 'nse_cm')\
                .limit(5)
        result = await conn.execute(query)
        return result.fetchall()

async def main():
    # 1. Get Tokens
    db_rows = await get_test_tokens()
    if not db_rows:
        logger.error("❌ No tokens found! Run morning_drill.py first.")
        return

    tokens = [str(row.instrument_token) for row in db_rows]
    symbols = {str(row.instrument_token): row.trading_symbol for row in db_rows}
    
    logger.info(f"🧪 Testing Feed for: {list(symbols.values())}")

    # 2. Start Feed
    await feed_engine.start(tokens)

    # 3. Consume Event Bus
    logger.info("👀 Watching Event Bus...")
    
    while True:
        tick_payload = await event_bus.tick_queue.get()
        
        # --- 🛠️ FIX: Handle the Dictionary Wrapper 🛠️ ---
        # Kotak sends: {'type': 'stock_feed', 'data': [ ...ticks... ]}
        ticks = []
        if isinstance(tick_payload, dict) and 'data' in tick_payload:
            ticks = tick_payload['data']
        elif isinstance(tick_payload, list):
            ticks = tick_payload
        
        if not ticks:
            # Print raw only if we couldn't parse it (e.g. heartbeat)
            # logger.debug(f"RAW: {tick_payload}")
            continue

        for tick in ticks:
            tk = str(tick.get('tk', ''))
            ltp = tick.get('ltp')
            
            if tk in symbols:
                print(f"⚡ {symbols[tk]}: ₹{ltp}")
        # ------------------------------------------------

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Stopped.")