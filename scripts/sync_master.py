from app.data.master import master_data
from app.core.logger import setup_logging
import asyncio
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.insert(0, project_root)


setup_logging()

if __name__ == "__main__":
    asyncio.run(master_data.sync_daily_script())
