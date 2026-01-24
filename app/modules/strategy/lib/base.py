import logging
from abc import ABC, abstractmethod
from datetime import datetime

class BaseStrategy(ABC):
    def __init__(self, name: str, symbol: str, token: str):
        self.name = name
        self.symbol = symbol
        self.token = str(token)
        self.logger = logging.getLogger(f"STRAT:{name}")
        
        # Candle Construction State
        self.current_candle = None
        self.candles = [] # List of closed 1-min candles
        
        # Risk State
        self.position = 0
        self.entry_price = 0.0

    async def on_tick(self, tick: dict):
        """
        Ingests a tick, updates the current candle, and calls `on_candle_close` if minute changes.
        """
        ltp = float(tick.get('ltp', 0))
        vol = int(tick.get('v', 0))
        ts = datetime.now() # In real prod, use tick timestamp if available

        # 1. Initialize Candle
        if not self.current_candle:
            self.current_candle = {
                'open': ltp, 'high': ltp, 'low': ltp, 'close': ltp,
                'volume': vol, 'start_time': ts.replace(second=0, microsecond=0)
            }
            return

        # 2. Check if Minute Changed (Candle Close)
        if ts.minute != self.current_candle['start_time'].minute:
            # Finalize old candle
            closed_candle = self.current_candle
            self.candles.append(closed_candle)
            
            # Keep only last 100 candles to save memory
            if len(self.candles) > 100:
                self.candles.pop(0)

            # Trigger Logic
            await self.on_candle_close(closed_candle)

            # Start New Candle
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
            c['volume'] += vol # Accumulate volume

    @abstractmethod
    async def on_candle_close(self, candle: dict):
        """Logic runs here every minute"""
        pass