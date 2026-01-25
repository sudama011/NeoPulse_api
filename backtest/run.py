import sys
import os
import asyncio
import logging

# Add project root to path so we can import from app/
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.insert(0, project_root)

from app.core.logger import setup_logging
from app.services.strategy.lib.momentum import MomentumStrategy
from app.services.strategy.lib.gap_fill import GapFillStrategy
from app.services.strategy.lib.mean_reversion import MeanReversionStrategy
from app.services.strategy.lib.orb import ORBStrategy

from backtest.engine import BacktestEngine
from backtest.config import BACKTEST_CONFIG

# Setup basic logging for the console
setup_logging()
logging.getLogger("yfinance").setLevel(logging.WARNING)

async def main():
    print("🚀 STARTING BACKTEST SUITE")
    print("=" * 30)
    print("1. Momentum Strategy")
    print("2. Gap Fill Strategy")
    print("3. Mean Reversion")
    print("4. ORB Strategy")
    print("5. Run ALL")
    
    choice = input("\nSelect Strategy (1-5): ")
    
    strategies_to_run = []
    
    if choice == "1":
        strategies_to_run.append(("MOMENTUM", MomentumStrategy))
    elif choice == "2":
        strategies_to_run.append(("GAP_FILL", GapFillStrategy))
    elif choice == "3":
        strategies_to_run.append(("MEAN_REVERSION", MeanReversionStrategy))
    elif choice == "4":
        strategies_to_run.append(("ORB", ORBStrategy))
    elif choice == "5":
        strategies_to_run = [
            ("MOMENTUM", MomentumStrategy),
            ("GAP_FILL", GapFillStrategy),
            ("MEAN_REVERSION", MeanReversionStrategy),
            ("ORB", ORBStrategy)
        ]
    else:
        print("❌ Invalid choice")
        return

    # Loop through selected strategies and symbols
    for strat_name, strat_class in strategies_to_run:
        params = BACKTEST_CONFIG["strategies"].get(strat_name, {})
        
        for symbol in BACKTEST_CONFIG["symbols"]:
            print(f"\n🏃 Running {strat_name} on {symbol}...")
            
            engine = BacktestEngine(
                symbol=symbol,
                strategy_class=strat_class,
                days=BACKTEST_CONFIG["days"],
                capital=BACKTEST_CONFIG["capital"],
                params=params
            )
            
            await engine.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Backtest stopped by user.")