import asyncio
import sys
import os
import logging

# Fix Path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.insert(0, project_root)

# Setup Logger
from app.core.logger import setup_logging
setup_logging()

from research.backtesting.engine import BacktestService

async def main():
    # Pick a volatile stock for Momentum testing
    # Note: Yahoo Finance requires '.NS' suffix for NSE stocks
    symbol = "RELIANCE.NS" 
    
    logger = logging.getLogger("Main")
    logger.info(f"🧪 Starting Backtest on {symbol}...")
    
    # Run for last 5 days
    engine = BacktestService(symbol, days=5)
    await engine.run()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())