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
        """
        ltp = float(tick.get('ltp', 0))
        vol = int(tick.get('v', 0))
        
        # Use provided timestamp or system time
        tick_time = tick.get('ts')
        ts = tick_time if tick_time else datetime.now()

        # 1. Initialize Candle
        if not self.current_candle:
            self._start_new_candle(ltp, vol, ts)
            return

        # 2. Check for Minute Change
        if ts.minute != self.current_candle['start_time'].minute:
            await self._close_current_candle()
            self._start_new_candle(ltp, vol, ts)
        else:
            # 3. Update Current Candle
            c = self.current_candle
            c['high'] = max(c['high'], ltp)
            c['low'] = min(c['low'], ltp)
            c['close'] = ltp
            c['volume'] += vol

    async def on_time_update(self, current_time: datetime):
        """
        Called by Heartbeat. 
        Forces candle closure if the minute has passed and we haven't received a new tick yet.
        """
        if not self.current_candle:
            return

        candle_minute = self.current_candle['start_time'].minute
        current_minute = current_time.minute
        
        # If we moved to a new minute, close the old one immediately
        # Logic: If candle is 9:15 and it is now 9:16:01, close 9:15.
        if current_minute != candle_minute:
            # We don't have a new LTP, so we just close the old one.
            # We do NOT start a new one until a tick arrives.
            await self._close_current_candle()

    async def _close_current_candle(self):
        if not self.current_candle:
            return
            
        closed_candle = self.current_candle
        self.candles.append(closed_candle)
        
        if len(self.candles) > 100:
            self.candles.pop(0)

        self.current_candle = None # Reset
        
        # Trigger Strategy Logic
        await self.on_candle_close(closed_candle)

    def _start_new_candle(self, ltp, vol, ts):
        self.current_candle = {
            'open': ltp, 'high': ltp, 'low': ltp, 'close': ltp,
            'volume': vol, 'start_time': ts.replace(second=0, microsecond=0)
        }

    @abstractmethod
    async def on_candle_close(self, candle: dict):
        pass
    
    # Optional hook for order updates
    async def on_order_update(self, data: dict):
        pass