
import os
import sys
import asyncio

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.insert(0, project_root)

from app.data.master import master_data
from app.core.logger import setup_logging

setup_logging()

if __name__ == "__main__":
    asyncio.run(master_data.sync_daily_script())
