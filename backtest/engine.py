import asyncio
import logging
import os
import sys

# Ensure project root is in path for standalone execution
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.execution.engine import execution_engine
from app.strategy import get_strategy_class, list_strategies
from app.strategy.base import BaseStrategy
from backtest.analyst import PerformanceAnalyst
from backtest.feed import HistoricalFeed
from backtest.simulator import BacktestBroker

logger = logging.getLogger("BacktestEngine")


class BacktestEngine:
    """
    Backtest orchestrator.
    - Uses the Strategy Registry to create any registered strategy.
    - Monkey-patches the execution engine to route orders to BacktestBroker.
    - Feeds historical candles via on_candle().
    """

    def __init__(
        self,
        symbol: str,
        strategy_name: str,
        days: int = 30,
        interval: str = "5m",
        initial_capital: float = 100_000.0,
        strategy_params: dict = None,
    ):
        self.symbol = symbol
        self.strategy_name = strategy_name.upper()
        self.feed = HistoricalFeed(symbol, days, interval)
        self.broker = BacktestBroker(initial_capital=initial_capital)

        # Create strategy from registry
        strategy_cls = get_strategy_class(self.strategy_name)
        self.strategy: BaseStrategy = strategy_cls(
            name=f"BT_{self.strategy_name}_{symbol}",
            symbol=symbol,
            token="BT_TOKEN",
            params=strategy_params or {},
        )
        # Mark as backtest mode
        self.strategy._backtest_mode = True

    async def run(self) -> dict:
        """Main Backtest Loop."""
        logger.info(f"🚀 Starting Backtest: {self.strategy_name} on {self.symbol}")

        # 1. Load Data
        self.feed.load_data()
        if self.feed.data.empty:
            return {"error": "No historical data loaded."}

        # 2. Inject Simulator into Execution Layer
        original_broker = execution_engine.broker
        execution_engine.broker = self.broker

        try:
            # 3. Stream candles to strategy
            candle_count = 0
            for candle in self.feed.stream():
                # Update Broker's pricing view
                self.broker.update_candle(candle)

                # Feed candle to strategy via safe handler
                await self.strategy.safe_on_candle(candle)
                candle_count += 1

                if not self.strategy.is_active:
                    logger.warning(f"⚠️ Strategy deactivated after {candle_count} candles.")
                    break

            logger.info(f"✅ Processed {candle_count} candles.")

            # 4. Generate Report
            analyst = PerformanceAnalyst(self.broker.initial_capital, self.broker.orders)
            report = analyst.generate_report()
            report["symbol"] = self.symbol
            report["strategy"] = self.strategy_name
            report["candles_processed"] = candle_count

            return report

        finally:
            # 5. Always restore execution engine
            execution_engine.broker = original_broker


# --- CLI Helper ---
async def run_backtest(
    symbol: str = "RELIANCE",
    strategy: str = "MACD_VOLUME",
    days: int = 30,
    interval: str = "5m",
    capital: float = 100_000.0,
    params: dict = None,
):
    """Convenience function to run a backtest and print results."""
    print(f"\n📋 Available strategies: {list_strategies()}")

    engine = BacktestEngine(
        symbol=symbol,
        strategy_name=strategy,
        days=days,
        interval=interval,
        initial_capital=capital,
        strategy_params=params,
    )
    report = await engine.run()

    if "error" in report:
        print(f"\n❌ {report['error']}")
        return report

    # Pretty print
    analyst = PerformanceAnalyst(capital, [])
    analyst.print_report(report)

    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NeoPulse Backtester")
    parser.add_argument("--symbol", default="RELIANCE", help="Stock symbol (e.g., RELIANCE)")
    parser.add_argument("--strategy", default="MACD_VOLUME", help="Strategy name from registry")
    parser.add_argument("--days", type=int, default=30, help="Number of days of history")
    parser.add_argument("--interval", default="5m", help="Candle interval (1m, 5m, 15m, 1d)")
    parser.add_argument("--capital", type=float, default=100_000, help="Initial capital")
    args = parser.parse_args()

    try:
        asyncio.run(
            run_backtest(
                symbol=args.symbol,
                strategy=args.strategy,
                days=args.days,
                interval=args.interval,
                capital=args.capital,
            )
        )
    except KeyboardInterrupt:
        print("\n🛑 Backtest stopped by user.")
