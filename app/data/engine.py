import asyncio
import logging

from app.data.feed import market_feed
from app.data.master import master_data
from app.data.stream import data_stream

logger = logging.getLogger("DataEngine")


class DataEngine:
    """
    Orchestrates the entire Data Layer.
    """

    async def initialize(self):
        """
        Startup Sequence:
        1. Load Master Data (Tokens)
        2. Start Event Bus Consumer
        3. Connect Feed
        """
        logger.info("💾 Initializing Data Layer...")

        # 1. Load Tokens
        await master_data.initialize()

        # 2. Start Bus Consumer (Background Task)
        asyncio.create_task(data_stream.consume())

        # 3. Connect Feed
        await market_feed.connect()

        logger.info("✅ Data Layer Ready")

    async def subscribe_strategies(self, tokens: list):
        """
        Helper for strategies to request data.
        """
        await market_feed.subscribe(tokens)


# Global
data_engine = DataEngine()
