import asyncio
import logging
from datetime import datetime
from app.services.strategy.manager import strategy_engine
from app.core.constants import MARKET_OPEN_TIME, MARKET_CLOSE_TIME, IST


import holidays 
logger = logging.getLogger("Scheduler")

india_holidays = holidays.IN(years=datetime.now().year)

class MarketScheduler:
    def __init__(self):
        self.market_open = MARKET_OPEN_TIME
        self.market_close = MARKET_CLOSE_TIME

    def is_market_open_now(self) -> bool:
        now = datetime.now(IST)
        
        # 1. Check Weekend (Saturday=5, Sunday=6)
        # Note: Need special logic for "Budget Sunday" manual override later
        if now.weekday() >= 5: 
            return False

        # 2. Check National Holidays
        if now.date() in india_holidays:
            return False

        # 3. Check Time
        current_time = now.time()
        return self.market_open <= current_time <= self.market_close

    async def run_loop(self):
        """
        Runs forever. Starts/Stops Strategy based on time.
        """
        logger.info("🕰️ Scheduler Started.")
        
        while True:
            if self.is_market_open_now():
                if not strategy_engine.is_running:
                    logger.info("🔔 Market Open! Starting Strategies.")
                    await strategy_engine.start()
            else:
                if strategy_engine.is_running:
                    logger.info("🔔 Market Closed. Stopping Strategies.")
                    strategy_engine.is_running = False
            
            # Check every minute
            await asyncio.sleep(60)