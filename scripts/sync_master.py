import asyncio
import os
import sys

# Add project root to path so we can import 'app'
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.insert(0, project_root)

from app.core.logger import setup_logging
from app.data.master import master_data

setup_logging()

if __name__ == "__main__":
    asyncio.run(master_data.sync_daily_script())
