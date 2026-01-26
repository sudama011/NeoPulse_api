import logging
from abc import ABC, abstractmethod
from datetime import datetime
from app.core.executors import run_blocking
from app.risk.manager import RiskManager
from app.execution.engine import execution_engine

class BaseStrategy(ABC):
    def __init__(self, name: str, symbol: str, token: str, risk_manager: RiskManager):
        self.name = name
        self.symbol = symbol
        self.token = str(token)
        self.risk_manager = risk_manager
        self.logger = logging.getLogger(f"Strat:{name}:{symbol}")
        
        # State
        self.position = 0
        self.entry_price = 0.0
        self.candles = []
        self.current_candle = None

        # Trailing Stop Configuration
        self.trailing_enabled = False
        self.trailing_active = False
        self.trailing_high = 0.0
        self.trailing_activation_pct = 0.005 # 0.5% Profit triggers trailing
        self.trailing_sl_pct = 0.003       # 0.3% gap

    # --- CORE FLOW ---
    async def on_tick(self, tick: dict):
        """Ingests live ticks, aggregates candles, checks trailing SL."""
        ltp = float(tick['ltp'])
        ts = datetime.now() # Use server time for consistent boundaries
        
        # 1. Update Trailing Stop (Live Tick Level)
        if self.position != 0 and self.trailing_enabled:
            await self._check_trailing_stop(ltp)

        # 2. Candle Aggregation
        if not self.current_candle:
            self._start_candle(ltp, tick.get('v', 0), ts)
            return

        # Check for new minute
        if ts.minute != self.current_candle['start_time'].minute:
            await self._close_candle()
            self._start_candle(ltp, tick.get('v', 0), ts)
        else:
            c = self.current_candle
            c['high'] = max(c['high'], ltp)
            c['low'] = min(c['low'], ltp)
            c['close'] = ltp
            c['volume'] += tick.get('v', 0)

    # --- HELPERS ---
    def _start_candle(self, ltp, vol, ts):
        self.current_candle = {
            'open': ltp, 'high': ltp, 'low': ltp, 'close': ltp,
            'volume': vol, 'start_time': ts.replace(second=0, microsecond=0)
        }

    async def _close_candle(self):
        if not self.current_candle: return
        candle = self.current_candle
        self.candles.append(candle)
        if len(self.candles) > 200: self.candles.pop(0) # Keep memory light
        self.current_candle = None
        
        # Trigger Strategy Logic
        await self.logic(candle)

    async def _check_trailing_stop(self, ltp):
        """Dynamic Trailing Stop Logic"""
        if self.position > 0: # LONG
            if ltp > self.entry_price * (1 + self.trailing_activation_pct):
                self.trailing_active = True
            
            if self.trailing_active:
                if ltp > self.trailing_high:
                    self.trailing_high = ltp
                
                sl_price = self.trailing_high * (1 - self.trailing_sl_pct)
                if ltp < sl_price:
                    self.logger.info(f"⛓️ Trailing SL Hit @ {ltp}")
                    await self.execute_order("SELL", ltp, "TRAILING_SL")

    async def execute_order(self, side: str, price: float, tag: str = "SIGNAL"):
        """Calculates size and executes order."""
        qty = 0
        
        # Closing Position
        if (side == "SELL" and self.position > 0) or (side == "BUY" and self.position < 0):
            qty = abs(self.position)
        
        # Opening Position
        elif self.position == 0:
            sl_price = price * 0.995 # Default 0.5% SL for sizing
            qty = self.risk_manager.calculate_size(
                capital=100000, # Ideally passed from engine config
                entry=price, 
                sl=sl_price
            )
            if qty < 1: return

        if qty > 0:
            resp = await execution_engine.execute_order(self.symbol, self.token, side, qty)
            if resp and resp.get('status') == 'success':
                self.position = qty if side == "BUY" else -qty
                self.entry_price = price
                self.trailing_high = price # Reset trailing
                self.logger.info(f"✅ Executed {side} {qty} @ {price} [{tag}]")

    @abstractmethod
    async def logic(self, candle: dict):
        """User logic goes here"""
        pass