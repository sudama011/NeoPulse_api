import asyncio
import logging
import sys
import os


current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.insert(0, project_root)

from backtest.feed import HistoricalFeed
from backtest.simulator import BacktestBroker
from backtest.analyst import PerformanceAnalyst

from app.strategy.strategies import MomentumStrategy, ORBStrategy # Import your strategies
from app.execution.engine import execution_engine

# We need to Monkey Patch the execution engine to use our Simulator
# This allows the Strategy to call 'execution_engine.execute_order' 
# and have it routed to our backtest broker transparently.

logger = logging.getLogger("BacktestEngine")

class BacktestEngine:
    def __init__(self, symbol: str, strategy_name: str, days: int = 30):
        self.symbol = symbol
        self.feed = HistoricalFeed(symbol, days)
        self.broker = BacktestBroker(initial_capital=100000)
        
        # Strategy Factory
        if strategy_name == "MOMENTUM":
            self.strategy = MomentumStrategy(symbol, "BT_TOKEN")
        elif strategy_name == "ORB":
            self.strategy = ORBStrategy(symbol, "BT_TOKEN")
        else:
            raise ValueError("Unknown Strategy")

    async def run(self):
        """Main Backtest Loop."""
        logger.info("🚀 Starting Backtest...")
        
        # 1. Load Data
        self.feed.load_data()
        if self.feed.data.empty: return

        # 2. Inject Simulator into Execution Layer
        # This is the 'magic' that makes the strategy use the fake broker
        original_broker = execution_engine.broker
        execution_engine.broker = self.broker

        # 3. Loop Through History
        for candle in self.feed.stream():
            # Update Broker's pricing view (so orders fill at current price)
            self.broker.update_candle(candle)
            
            # Feed Candle to Strategy
            # Note: Strategies usually aggregate ticks. 
            # Since we have full candles, we can bypass 'on_tick' and call logic directly
            # OR simulate ticks. For speed, we push the candle directly if the strategy supports it.
            
            # Manually append to strategy's history
            self.strategy.candles.append(candle)
            if len(self.strategy.candles) > 200: 
                self.strategy.candles.pop(0)
            
            # Execute Logic
            await self.strategy.logic(candle)

        # 4. Generate Report
        analyst = PerformanceAnalyst(self.broker.initial_capital, self.broker.orders)
        report = analyst.generate_report()
        
        # 5. Restore Execution Engine
        execution_engine.broker = original_broker
        
        return report

# --- Helper to run it easily ---
async def run_backtest(symbol="RELIANCE", strategy="MOMENTUM"):
    engine = BacktestEngine(symbol, strategy)
    report = await engine.run()
    
    print("\n" + "="*40)
    print(f"📊 BACKTEST RESULTS: {symbol}")
    print("="*40)
    print(f"💰 Final Equity:  ₹{report['final_equity']}")
    print(f"📈 Total Return:  {report['return_pct']}")
    print(f"📉 Max Drawdown:  {report['max_drawdown']}")
    print(f"🔢 Total Trades:  {report['total_trades']}")
    print("="*40 + "\n")

if __name__ == "__main__":
    try:
        asyncio.run(run_backtest("RELIANCE", "ORB"))
    except KeyboardInterrupt:
        print("\n🛑 Backtest stopped by user.")