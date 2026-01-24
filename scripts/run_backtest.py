import asyncio
import sys
import os
import logging

# 1. Fix Path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.insert(0, project_root)

# 2. Setup Logger
from app.core.logger import setup_logging
setup_logging()

from app.services.backtest import BacktestEngine

async def main():
    # Initialize Engine
    engine = BacktestEngine()
    
    # Create Data
    engine.generate_sine_wave_data()
    
    # Run Simulation
    await engine.run()

if __name__ == "__main__":
    asyncio.run(main())