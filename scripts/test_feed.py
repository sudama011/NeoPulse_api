import asyncio
import logging
import os
import sys

# Add project root to path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.insert(0, project_root)

from app.core.executors import global_executor
from app.core.logger import setup_logging
from app.core.settings import settings
from app.data.feed import market_feed
from app.data.master import master_data
from app.data.stream import data_stream
from app.execution.kotak import kotak_adapter

# Configure Logging to show INFO
setup_logging()
logger = logging.getLogger("TestFeed")

# TEST CONFIGURATION
TEST_SYMBOL = "RELIANCE-EQ"  # Change this to any active symbol
DURATION = 30  # Seconds to run


async def main():
    logger.info("🚀 Starting Feed Test Script...")

    try:
        # 1. Start Infrastructure
        global_executor.start()

        # 2. Load Master Data (to resolve Symbol -> Token)
        await master_data.initialize()

        # 3. Login to Broker (if not Paper Trading)
        if not settings.PAPER_TRADING:
            logger.info("🔐 Logging into Kotak Neo...")
            await kotak_adapter.login()
        else:
            logger.info("📝 Running in PAPER MODE (Virtual Broker)")

        # 4. Resolve Token
        token = master_data.get_token(TEST_SYMBOL)
        if not token:
            logger.error(f"❌ Symbol {TEST_SYMBOL} not found in Master Data! Run 'make sync' first.")
            return

        logger.info(f"✅ Resolved {TEST_SYMBOL} -> Token: {token}")

        # 5. Start Feed Connection (Background)
        feed_task = asyncio.create_task(market_feed.connect())

        # 6. Subscribe to the Token via Feed
        # The feed manager handles the actual socket subscription
        await market_feed.subscribe([token])

        # 7. Subscribe to the Event Bus (DataStream) to receive ticks
        # This mocks what a Strategy would do
        logger.info(f"👂 Listening for ticks on DataStream for {DURATION} seconds...")

        subscription = await data_stream.subscribe(token)

        async with subscription:
            end_time = asyncio.get_running_loop().time() + DURATION

            while asyncio.get_running_loop().time() < end_time:
                try:
                    # Wait for tick with timeout so we can check loop condition
                    tick = await asyncio.wait_for(subscription.get(), timeout=1.0)

                    # Print formatted tick
                    ltp = tick.get("ltp", tick.get("last_price", "N/A"))
                    vol = tick.get("vol", tick.get("volume", "N/A"))
                    ts = tick.get("ltt", "N/A")

                    print(f"📈 TICK: {TEST_SYMBOL} | Price: {ltp} | Vol: {vol} | Time: {ts}")

                except asyncio.TimeoutError:
                    continue  # Loop back to check time

    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by User")
    except Exception as e:
        logger.exception(f"❌ Test Failed: {e}")
    finally:
        # 8. Clean Shutdown
        logger.info("🛑 Shutting down...")
        market_feed._stop_event.set()

        # Wait for feed to close (optional)
        # await asyncio.sleep(1)

        global_executor.stop()
        logger.info("👋 Done.")


if __name__ == "__main__":
    asyncio.run(main())
