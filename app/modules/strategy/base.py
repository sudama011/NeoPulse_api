import logging
from abc import ABC, abstractmethod
from datetime import datetime

class BaseStrategy(ABC):
    def __init__(self, name: str, symbol: str, token: str):
        self.name = name
        self.symbol = symbol
        self.token = str(token)
        self.logger = logging.getLogger(f"STRAT:{name}")
        
        # Candle Construction
        self.current_candle = None
        self.candles = [] 
        
        # Risk State
        self.position = 0
        self.entry_price = 0.0

    async def on_tick(self, tick: dict):
        """
        Ingests a tick.
        CRITICAL CHANGE: Uses tick['ts'] for time, not datetime.now()
        """
        ltp = float(tick.get('ltp', 0))
        vol = int(tick.get('v', 0))
        
        # 🟢 TIME TRAVEL FIX:
        # If the tick has a timestamp (backtest), use it. 
        # Otherwise (live), use system time.
        tick_time = tick.get('ts')
        if tick_time:
            # Assume tick['ts'] is a datetime object
            ts = tick_time
        else:
            ts = datetime.now()

        # 1. Initialize Candle
        if not self.current_candle:
            self.current_candle = {
                'open': ltp, 'high': ltp, 'low': ltp, 'close': ltp,
                'volume': vol, 'start_time': ts.replace(second=0, microsecond=0)
            }
            return

        # 2. Check for Minute Change (using the tick's time)
        # Note: In backtest, we might jump from 09:15:59 to 09:16:01
        if ts.minute != self.current_candle['start_time'].minute:
            # A. Close the old candle
            closed_candle = self.current_candle
            self.candles.append(closed_candle)
            
            # Keep memory clean
            if len(self.candles) > 100:
                self.candles.pop(0)

            # B. Trigger Logic
            await self.on_candle_close(closed_candle)

            # C. Start New Candle
            self.current_candle = {
                'open': ltp, 'high': ltp, 'low': ltp, 'close': ltp,
                'volume': vol, 'start_time': ts.replace(second=0, microsecond=0)
            }
        else:
            # 3. Update Current Candle
            c = self.current_candle
            c['high'] = max(c['high'], ltp)
            c['low'] = min(c['low'], ltp)
            c['close'] = ltp
            c['volume'] += vol
            # Update volume logic can be complex in snapshots, 
            # but for simple backtest this is fine.

    @abstractmethod
    async def on_candle_close(self, candle: dict):
        pass