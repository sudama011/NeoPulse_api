import asyncio
import os
import sys

# Add project root to path so we can import 'app'
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.insert(0, project_root)

# 1. Import the global executor
from app.core.executors import global_executor
from app.core.logger import setup_logging
from app.data.master import master_data

setup_logging()


async def main():
    try:
        # 2. Start the Thread Pool
        global_executor.start()

        # 3. Run the Sync
        await master_data.sync_daily_script()

    finally:
        # 4. Clean Shutdown
        global_executor.stop()


if __name__ == "__main__":
    asyncio.run(main())
