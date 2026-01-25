"""
Main Backtest Runner - Orchestrates all strategy backtests

Executes backtests for all configured strategies across their assigned stocks.
Uses real yfinance data with retry logic and generates comprehensive performance reports.
"""

import asyncio
import sys
import os
import logging
import json
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# Fix Path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..', '..'))
sys.path.insert(0, project_root)

# Setup Logger
from app.core.logger import setup_logging
setup_logging()

from app.services.strategy.lib.momentum import MomentumStrategy
from app.services.strategy.lib.gap_fill import GapFillStrategy
from app.services.strategy.lib.mean_reversion import MeanReversionStrategy
from app.services.strategy.lib.orb import ORBStrategy
from app.services.risk.position_sizer import CapitalManager
from research.config.strategy_config import STRATEGY_CONFIG, BACKTEST_CONFIG
from research.backtesting.engine import BacktestService

logger = logging.getLogger("BacktestOrchestrator")

# Strategy-to-class mapping
STRATEGY_CLASS_MAP = {
    "MOMENTUM_TREND": MomentumStrategy,
    "GAP_FILL": GapFillStrategy,
    "MEAN_REVERSION": MeanReversionStrategy,
    "OPENING_RANGE_BREAKOUT": ORBStrategy,
}


class BacktestOrchestrator:
    """Orchestrates backtests across all strategies and symbols."""
    
    def __init__(self):
        self.results = {}
        self.total_trades = 0
        self.total_wins = 0
        self.total_profit = 0.0
        self.yf_config = BACKTEST_CONFIG
    
    def run_single_backtest(
        self,
        strategy_name: str,
        strategy_class,
        symbol: str,
        params: dict,
        capital_manager: CapitalManager
    ) -> dict:
        """Run a single backtest for a strategy-symbol pair."""
        try:
            logger.info(f"🚀 Starting backtest: {strategy_name} on {symbol}")
            
            # Create backtest service
            backtest = BacktestService(
                strategy_class=strategy_class,
                capital_manager=capital_manager,
                initial_capital=BACKTEST_CONFIG["initial_capital"]
            )
            
            # Run backtest with real yfinance data
            report = backtest.run(
                symbol=f"{symbol}.NS",  # Add .NS suffix for NSE stocks
                start_date=BACKTEST_CONFIG.get("start_date", "2023-01-01"),
                end_date=BACKTEST_CONFIG.get("end_date", "2024-01-01"),
                use_provided_data=False,  # Use yfinance data
                strategy_params=params
            )
            
            logger.info(f"✅ Completed: {strategy_name} on {symbol}")
            logger.info(f"   Trades: {report.get('total_trades', 0)} | "
                       f"Win Rate: {report.get('win_rate', 0):.1f}% | "
                       f"PnL: ₹{report.get('total_pnl', 0):.0f}")
            
            return report
            
        except Exception as e:
            logger.error(f"❌ Backtest failed: {strategy_name} on {symbol}")
            logger.error(f"   Error: {str(e)}")
            return {
                "symbol": symbol,
                "strategy": strategy_name,
                "error": str(e),
                "total_trades": 0,
                "total_pnl": 0,
                "win_rate": 0,
            }
    
    async def run_all_backtests(self) -> None:
        """Run backtests for all configured strategy-symbol combinations."""
        start_time = datetime.now()
        logger.info("=" * 70)
        logger.info("🏁 NEOPULSE BACKTEST RUNNER - STARTING FULL RUN")
        logger.info(f"📊 Configuration loaded: {len(STRATEGY_CONFIG)} strategies")
        logger.info("=" * 70)
        
        # Create capital manager
        capital_manager = CapitalManager(
            total_capital=BACKTEST_CONFIG["initial_capital"],
            risk_pct=BACKTEST_CONFIG["risk_per_trade_pct"]
        )
        
        all_results = {}
        
        # Iterate through each strategy
        for strategy_name, config in STRATEGY_CONFIG.items():
            if strategy_name not in STRATEGY_CLASS_MAP:
                logger.warning(f"⚠️ Strategy {strategy_name} not found in class map, skipping")
                continue
                
            strategy_class = STRATEGY_CLASS_MAP[strategy_name]
            symbols = config.get("stocks", [])
            params = config.get("parameters", {})
            
            logger.info(f"\n{'=' * 70}")
            logger.info(f"📈 Strategy: {strategy_name}")
            logger.info(f"   Stocks: {', '.join(symbols)}")
            logger.info(f"{'=' * 70}")
            
            strategy_results = {}
            
            # Run backtest for each symbol assigned to this strategy
            for symbol in symbols:
                result = self.run_single_backtest(
                    strategy_name=strategy_name,
                    strategy_class=strategy_class,
                    symbol=symbol,
                    params=params,
                    capital_manager=capital_manager
                )
                strategy_results[symbol] = result
                
                # Aggregate stats
                if "error" not in result:
                    self.total_trades += result.get("total_trades", 0)
                    self.total_profit += result.get("total_pnl", 0)
                    if result.get("total_pnl", 0) > 0:
                        self.total_wins += 1
            
            all_results[strategy_name] = strategy_results
        
        # Generate final report
        self._generate_report(all_results, start_time)
    
    def _generate_report(self, results: dict, start_time: datetime) -> None:
        """Generate comprehensive backtest report."""
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        logger.info(f"\n{'=' * 70}")
        logger.info("📊 BACKTEST RESULTS SUMMARY")
        logger.info(f"{'=' * 70}")
        
        # Summary statistics
        logger.info(f"\n⏱️  Duration: {duration:.1f} seconds")
        logger.info(f"💰 Total Trades: {self.total_trades}")
        logger.info(f"✅ Profitable Backtests: {self.total_wins}/{len(STRATEGY_CONFIG)}")
        logger.info(f"📈 Total PnL: ₹{self.total_profit:.2f}")
        
        # Strategy-specific results
        logger.info(f"\n{'─' * 70}")
        logger.info("STRATEGY BREAKDOWN:")
        logger.info(f"{'─' * 70}")
        
        for strategy_name, symbol_results in results.items():
            logger.info(f"\n{strategy_name}:")
            
            strategy_trades = 0
            strategy_pnl = 0.0
            successful = 0
            
            for symbol, result in symbol_results.items():
                if "error" in result:
                    logger.info(f"  ❌ {symbol}: {result['error']}")
                else:
                    trades = result.get("total_trades", 0)
                    pnl = result.get("total_pnl", 0)
                    win_rate = result.get("win_rate", 0)
                    
                    strategy_trades += trades
                    strategy_pnl += pnl
                    if pnl > 0:
                        successful += 1
                    
                    status = "✅" if pnl > 0 else "❌"
                    logger.info(f"  {status} {symbol}: {trades} trades | "
                               f"Win Rate: {win_rate:.1f}% | PnL: ₹{pnl:.2f}")
            
            logger.info(f"  📊 Strategy Total: {strategy_trades} trades | "
                       f"Profitable: {successful}/{len(symbol_results)} | "
                       f"PnL: ₹{strategy_pnl:.2f}")
        
        # Save results to file
        self._save_results(results, start_time, end_time)
    
    def _save_results(self, results: dict, start_time: datetime, end_time: datetime) -> None:
        """Save detailed results to JSON file."""
        results_dir = Path("research/backtest_results")
        results_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = start_time.strftime("%Y%m%d_%H%M%S")
        results_file = results_dir / f"backtest_{timestamp}.json"
        
        report = {
            "timestamp": timestamp,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "duration_seconds": (end_time - start_time).total_seconds(),
            "summary": {
                "total_trades": self.total_trades,
                "total_profit": self.total_profit,
                "profitable_strategies": self.total_wins,
                "initial_capital": BACKTEST_CONFIG["initial_capital"],
            },
            "results": results,
        }
        
        with open(results_file, "w") as f:
            json.dump(report, f, indent=2, default=str)
        
        logger.info(f"\n💾 Results saved to: {results_file}")


async def main():
    """Main entry point."""
    try:
        orchestrator = BacktestOrchestrator()
        await orchestrator.run_all_backtests()
        logger.info("\n✨ Backtest run completed successfully!")
        
    except KeyboardInterrupt:
        logger.info("\n⚠️ Backtest interrupted by user")
    except Exception as e:
        logger.error(f"\n❌ Fatal error: {str(e)}", exc_info=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n\n🛑 Backtest interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"\n\n❌ Backtest failed: {e}")
        sys.exit(1)
