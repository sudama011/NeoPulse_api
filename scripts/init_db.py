# scripts/init_db.py
import asyncio
import os
import sys


# Add project root to path so we can import 'app'
sys.path.append(os.getcwd())

from app.db.init_db import init_db

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(init_db())
