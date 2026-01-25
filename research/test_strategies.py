"""
Individual strategy test runner with detailed performance analysis.
Test each strategy separately with reporting.
"""

import asyncio
import sys
import os
import logging

# Fix Path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.insert(0, project_root)

# Setup Logger
from app.core.logger import setup_logging
setup_logging()

from research.backtesting.engine import BacktestService

logger = logging.getLogger("IndividualStrategyTest")


class StrategyBacktestRunner:
    """Run individual strategy backtests with detailed metrics."""
    
    STRATEGY_CONFIGS = {
        "momentum": {
            "display_name": "Momentum Trend Following",
            "symbol": "RELIANCE.NS",
            "days": 7,
            "description": """
            Strategy: Momentum Trend Following
            - Entry: Price > EMA(50) AND RSI > 60 AND Price > VWAP
            - Exit: TP +0.9% or SL -0.3%
            - Position Size: Dynamic (based on capital)
            - Risk per Trade: 1%
            """
        },
        "gap_fill": {
            "display_name": "Gap Fill (Mean Reversion)",
            "symbol": "RELIANCE.NS",
            "days": 7,
            "description": """
            Strategy: Gap Fill
            - Entry: Price < Previous Close AND Price < SMA(20)
            - Exit: TP +0.5% or SL -0.4%
            - Position Size: Dynamic
            - Risk per Trade: 1%
            """
        },
        "mean_reversion": {
            "display_name": "Bollinger Bands Mean Reversion",
            "symbol": "RELIANCE.NS",
            "days": 7,
            "description": """
            Strategy: Mean Reversion (Bollinger Bands)
            - Entry: Price < Lower BB AND RSI < 30 (or overbought)
            - Exit: TP +0.6% or SL -0.35%
            - Position Size: Dynamic
            - Risk per Trade: 1%
            """
        },
        "orb": {
            "display_name": "Opening Range Breakout",
            "symbol": "RELIANCE.NS",
            "days": 7,
            "description": """
            Strategy: Opening Range Breakout (ORB)
            - Setup: First 15 minutes establish trading range
            - Entry: Breakout above high or below low
            - Exit: TP +0.7% or SL -0.4%
            - Position Size: Dynamic
            - Risk per Trade: 1%
            """
        }
    }

    async def run_strategy_test(self, strategy_key: str):
        """Run test for a specific strategy."""
        if strategy_key not in self.STRATEGY_CONFIGS:
            logger.error(f"Unknown strategy: {strategy_key}")
            return
        
        config = self.STRATEGY_CONFIGS[strategy_key]
        
        logger.info("\n")
        logger.info("╔" + "="*68 + "╗")
        logger.info("║" + f" {config['display_name']:^66} " + "║")
        logger.info("╚" + "="*68 + "╝")
        
        logger.info(f"\n📋 Strategy Details:")
        logger.info(config['description'])
        
        logger.info(f"\n⚙️ Test Configuration:")
        logger.info(f"  Symbol: {config['symbol']}")
        logger.info(f"  Duration: {config['days']} days")
        logger.info(f"  Initial Capital: ₹100,000")
        
        logger.info(f"\n🔄 Running backtest...")
        logger.info(f"{'-'*68}")
        
        try:
            engine = BacktestService(
                symbol=config['symbol'],
                days=config['days']
            )
            await engine.run()
            
        except Exception as e:
            logger.error(f"❌ Backtest failed: {e}")
            logger.info("\n💡 Troubleshooting:")
            logger.info("  1. Check internet connection (for data download)")
            logger.info("  2. Verify symbol is correct")
            logger.info("  3. yfinance may be rate-limited - try again in 5 mins")

    async def run_all_tests(self):
        """Run all strategy tests sequentially."""
        logger.info("\n")
        logger.info("╔" + "="*68 + "╗")
        logger.info("║" + " "*12 + "🧪 NEOPULSE INDIVIDUAL STRATEGY BACKTESTS" + " "*13 + "║")
        logger.info("╚" + "="*68 + "╝")
        
        logger.info("\n📊 Available Strategies:")
        for idx, (key, config) in enumerate(self.STRATEGY_CONFIGS.items(), 1):
            logger.info(f"  {idx}. {config['display_name']} ({key})")
        
        logger.info(f"\n💡 Running tests for all strategies...")
        
        for strategy_key, config in self.STRATEGY_CONFIGS.items():
            await self.run_strategy_test(strategy_key)
            logger.info("\n")
            await asyncio.sleep(2)  # Wait between tests
        
        logger.info("="*68)
        logger.info("✅ All strategy tests completed!")
        logger.info(f"{'='*68}\n")


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="NeoPulse Strategy Backtest Runner")
    parser.add_argument(
        "--strategy",
        choices=["momentum", "gap_fill", "mean_reversion", "orb", "all"],
        default="all",
        help="Strategy to test (default: all)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to backtest (default: 7)"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="RELIANCE.NS",
        help="Stock symbol to test (default: RELIANCE.NS)"
    )
    
    args = parser.parse_args()
    
    runner = StrategyBacktestRunner()
    
    # Update config with CLI args
    for config in runner.STRATEGY_CONFIGS.values():
        if args.days:
            config['days'] = args.days
        if args.symbol:
            config['symbol'] = args.symbol
    
    if args.strategy == "all":
        await runner.run_all_tests()
    else:
        await runner.run_strategy_test(args.strategy)


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n\n🛑 Test runner interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"\n\n❌ Test runner failed: {e}")
        sys.exit(1)
