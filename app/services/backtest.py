import math
import random
import logging
from datetime import datetime, timedelta
from app.modules.strategy.lib.momentum import MomentumStrategy

logger = logging.getLogger("Backtester")

class BacktestEngine:
    def __init__(self):
        self.strategy = MomentumStrategy(symbol="TEST-STOCK", token="12345")
        self.ticks = []

    def generate_sine_wave_data(self):
        """
        Generates 3 hours of fake market data that moves in a sine wave.
        This GUARANTEES we get Buy/Sell signals (RSI will swing up and down).
        """
        logger.info("🎨 Generating Synthetic Market Data (Sine Wave)...")
        
        start_time = datetime(2024, 1, 1, 9, 15)
        base_price = 1000.0
        
        # Generate 3 hours of data (10,800 seconds)
        for i in range(10800):
            current_time = start_time + timedelta(seconds=i)
            
            # Math Magic: Sine wave to make price go Up and Down
            # Period = 3600 seconds (1 hour)
            oscillation = math.sin(i / 600) * 20 # Moves +/- 20 rupees
            noise = random.uniform(-2, 2) # Random market noise
            
            price = base_price + oscillation + noise
            volume = random.randint(50, 500)
            
            # Create a "Tick" similar to Kotak SDK
            tick = {
                'tk': '12345',
                'ltp': price,
                'v': volume,
                'ts': current_time # We inject the fake time
            }
            self.ticks.append(tick)
            
        logger.info(f"✅ Generated {len(self.ticks)} ticks.")

    async def run(self):
        """
        Feeds the fake data into the strategy.
        """
        logger.info("🚀 Starting Backtest...")
        
        for tick in self.ticks:
            # We call on_tick directly (bypassing EventBus for speed)
            await self.strategy.on_tick(tick)
            
        # Final Report
        pnl = (self.strategy.position * 0) # Close any open position at 0 (simplified)
        logger.info("🏁 Backtest Complete.")