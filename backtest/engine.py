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
from app.risk.manager import risk_manager as global_risk_manager
from app.strategy import get_strategy_class, list_strategies
from app.strategy.base import BaseStrategy
from backtest.analyst import PerformanceAnalyst
from backtest.plotter import plot_backtest, plot_backtest_interactive
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
            token="0",
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

        # 2. Inject Simulator into Execution Layer and patch Risk Manager for backtest
        original_broker = execution_engine.broker
        execution_engine.broker = self.broker

        # Patch Risk Manager to be permissive during backtest
        orig_can_trade = global_risk_manager.can_trade
        orig_calc_size = global_risk_manager.calculate_size
        orig_initialized = getattr(global_risk_manager, "is_initialized", False)

        async def _bt_can_trade(symbol: str, qty: int, price: float) -> bool:
            # Always allow in backtest
            return True

        async def _bt_calculate_size(symbol: str, entry: float, sl: float, confidence: float = 1.0) -> int:
            # Risk-based sizing with capital cap
            #  - Risk 1% * confidence per trade
            #  - Per-share risk = |entry - sl| (fallback to 0.5% of entry)
            #  - Cap by available capital so cost <= balance
            risk_pct = 0.01 * max(0.1, min(1.0, confidence))
            capital = float(getattr(self.broker, "balance", self.broker.initial_capital))
            risk_amt = max(1.0, capital * risk_pct)

            price_risk = abs(entry - sl) if sl and sl > 0 else max(0.005 * entry, 0.5)
            shares_by_risk = max(int(risk_amt / price_risk), 1)
            shares_by_cap = max(int(capital // max(entry, 1e-6)), 1)

            qty = min(shares_by_risk, shares_by_cap)
            return max(qty, 1)

        # Monkey patch
        global_risk_manager.can_trade = _bt_can_trade  # type: ignore
        global_risk_manager.calculate_size = _bt_calculate_size  # type: ignore
        global_risk_manager.is_initialized = True  # type: ignore

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

            # 4. Final Mark-to-Market snapshot so equity reflects last close
            try:
                last_ts = self.feed.data.index[-1]
                last_close = float(self.feed.data["close"].iloc[-1])
                eq = float(getattr(self.broker, "balance", self.broker.initial_capital))
                for sym, q in getattr(self.broker, "positions", {}).items():
                    eq += float(q) * last_close
                self.broker.orders.append({
                    "time": last_ts,
                    "symbol": self.symbol,
                    "side": "MARK",
                    "qty": 0,
                    "price": last_close,
                    "value": 0.0,
                    "balance_after": eq,
                    "equity_after": eq,
                    "position_after": getattr(self.broker, "positions", {}).get(self.symbol, 0),
                })
            except Exception:
                pass

            # 5. Generate Report
            analyst = PerformanceAnalyst(self.broker.initial_capital, self.broker.orders)
            report = analyst.generate_report()
            report["symbol"] = self.symbol
            report["strategy"] = self.strategy_name
            report["candles_processed"] = candle_count

            # 6. Chart: Candlesticks + MACD + Buy/Sell markers
            try:
                out_dir = os.path.join(current_dir, "output")
                fname = f"{self.strategy_name}_{self.symbol}_{self.feed.interval}_{self.feed.days}.png"
                macd_params = {}
                for k in ("fast", "slow", "signal"):
                    if hasattr(self.strategy, k):
                        macd_params[k] = getattr(self.strategy, k)
                plot_path = plot_backtest(
                    self.feed.data,
                    self.broker.orders,
                    title=f"{self.strategy_name} · {self.symbol} ({self.feed.interval})",
                    outfile=os.path.join(out_dir, fname),
                    macd_params=macd_params or None,
                )
                if plot_path:
                    report["plot_path"] = plot_path
            except Exception:
                pass

            return report

        finally:
            # 5. Always restore execution engine and risk manager
            execution_engine.broker = original_broker
            global_risk_manager.can_trade = orig_can_trade  # type: ignore
            global_risk_manager.calculate_size = orig_calc_size  # type: ignore
            global_risk_manager.is_initialized = orig_initialized  # type: ignore


# --- CLI Helper ---
async def run_backtest(
    symbol: str = "RELIANCE",
    strategy: str = "MACD_VOLUME",
    days: int = 30,
    interval: str = "5m",
    capital: float = 100_000.0,
    params: dict = None,
    interactive: bool = False,
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

    if report.get("plot_path"):
        print(f"\n🖼  Chart saved to: {report['plot_path']}")

    # Optional interactive chart (HTML) if requested and Plotly is available
    if interactive:
        try:
            out_dir = os.path.join(current_dir, "output")
            html_name = f"{strategy}_{symbol}_{interval}_{days}.html"
            macd_params = {}
            for k in ("fast", "slow", "signal"):
                if hasattr(engine.strategy, k):
                    macd_params[k] = getattr(engine.strategy, k)
            html_path = plot_backtest_interactive(
                engine.feed.data,
                engine.broker.orders,
                title=f"{strategy} · {symbol} ({interval})",
                outfile_html=os.path.join(out_dir, html_name),
                macd_params=macd_params or None,
            )
            if html_path:
                print(f"🧭 Interactive chart: {html_path}")
        except Exception:
            pass

    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NeoPulse Backtester")
    parser.add_argument("--symbol", default="HDFCBANK", help="Stock symbol (e.g., RELIANCE)")
    parser.add_argument("--strategy", default="MACD_VOLUME", help="Strategy name from registry")
    parser.add_argument("--days", type=int, default=59, help="Number of days of history")
    parser.add_argument("--interval", default="5m", help="Candle interval (1m, 5m, 15m, 1d)")
    parser.add_argument("--capital", type=float, default=100_000, help="Initial capital")
    parser.add_argument("--interactive", action="store_true", help="Generate interactive HTML chart (Plotly)")
    args = parser.parse_args()

    try:
        asyncio.run(
            run_backtest(
                symbol=args.symbol,
                strategy=args.strategy,
                days=args.days,
                interval=args.interval,
                capital=args.capital,
                interactive=args.interactive,
            )
        )
    except KeyboardInterrupt:
        print("\n🛑 Backtest stopped by user.")
