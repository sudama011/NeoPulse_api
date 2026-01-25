import logging
from datetime import timedelta, datetime
from typing import Optional
from app.services.strategy.base import BaseStrategy
from app.services.strategy.indicators import calculate_rsi, calculate_vwap, calculate_ema
from app.services.oms.executor import order_executor
from app.services.risk.position_sizer import CapitalManager
from app.core.executors import run_blocking


logger = logging.getLogger("MeanReversionStrategy")


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion Strategy using Bollinger Bands.
    
    Logic:
    - LONG: Price < Lower BB AND RSI < 30 (oversold)
    - SHORT: Price > Upper BB AND RSI > 70 (overbought)
    
    Risk Management:
    - Take Profit: +0.6%
    - Stop Loss: -0.35%
    - Cooldown: 8 mins after exit
    
    State Machine:
    1. FLAT (position == 0) → Enters on signal
    2. LONG (position > 0) → Exits on SL/TP
    3. SHORT (position < 0) → Exits on SL/TP
    4. COOLING → Waits before re-entry
    """

    def __init__(self, symbol: str, token: str, risk_monitor=None, capital_manager: Optional[CapitalManager] = None):
        super().__init__("MEAN_REVERSION", symbol, token, risk_monitor)
        
        # Capital Management
        self.capital_manager = capital_manager or CapitalManager(total_capital=100000.0, risk_per_trade_pct=0.01)
        
        # Indicator Configuration
        self.bb_period = 20
        self.bb_std_dev = 2.0
        self.rsi_period = 14
        
        # Risk Configuration
        self.stop_loss_pct = 0.0035      # 0.35% Risk
        self.take_profit_pct = 0.0060    # 0.6% Reward
        self.position_qty = 22           # Default qty

        # Cooldown Configuration
        self.cooldown_minutes = 8
        self.last_exit_time: Optional[datetime] = None
        
        # Trading State
        self.last_signal: Optional[str] = None
        self.last_signal_time: Optional[datetime] = None

    async def on_candle_close(self, candle: dict) -> None:
        """Processes closed 1-min candle."""
        if len(self.candles) < self.bb_period:
            return

        current_time = candle['start_time']
        
        # Check Cooldown
        if self.last_exit_time:
            time_diff = current_time - self.last_exit_time
            if time_diff < timedelta(minutes=self.cooldown_minutes):
                return

        # Calculate indicators (Thread-safe)
        rsi = await run_blocking(self._calculate_rsi, self.candles, self.rsi_period)
        bb_upper, bb_lower = await run_blocking(self._calculate_bollinger_bands, self.candles, self.bb_period, self.bb_std_dev)
        close = candle['close']

        # Entry Logic
        if self.position == 0:
            await self._check_entry_signals(close, rsi, bb_upper, bb_lower, current_time)
        # Exit Logic
        elif self.position != 0:
            await self._check_exit_signals(close, current_time)

    def _calculate_rsi(self, candles: list, period: int) -> float:
        """Calculate RSI (runs in thread pool)."""
        if len(candles) < period + 1:
            return 50.0
        
        closes = [c['close'] for c in candles]
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        
        gains = [d for d in deltas if d > 0]
        losses = [-d for d in deltas if d < 0]
        
        avg_gain = sum(gains[-period:]) / period if gains else 0
        avg_loss = sum(losses[-period:]) / period if losses else 0
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _calculate_bollinger_bands(self, candles: list, period: int, std_dev: float) -> tuple:
        """Calculate Bollinger Bands (runs in thread pool)."""
        if len(candles) < period:
            return 0.0, 0.0
        
        closes = [c['close'] for c in candles[-period:]]
        sma = sum(closes) / len(closes)
        
        variance = sum((c - sma) ** 2 for c in closes) / len(closes)
        std = variance ** 0.5
        
        bb_upper = sma + (std_dev * std)
        bb_lower = sma - (std_dev * std)
        
        return bb_upper, bb_lower

    async def _check_entry_signals(self, close: float, rsi: float, bb_upper: float, bb_lower: float, current_time) -> None:
        """Entry signals based on Bollinger Bands + RSI."""
        # Long Signal (oversold)
        if close < bb_lower and rsi < 30:
            self.logger.info(
                f"🚀 MEAN REVERSION BUY @ {close:.2f} "
                f"(BB_Lower={bb_lower:.2f}, RSI={rsi:.1f})"
            )
            self.last_signal = "BUY"
            self.last_signal_time = current_time
            await self.execute_trade("BUY", close)

        # Short Signal (overbought)
        elif close > bb_upper and rsi > 70:
            self.logger.info(
                f"🔻 MEAN REVERSION SELL @ {close:.2f} "
                f"(BB_Upper={bb_upper:.2f}, RSI={rsi:.1f})"
            )
            self.last_signal = "SELL"
            self.last_signal_time = current_time
            await self.execute_trade("SELL", close)

    async def _check_exit_signals(self, close: float, current_time) -> None:
        """Check for exit signals based on PnL targets."""
        if self.position > 0:
            pnl_pct = (close - self.entry_price) / self.entry_price
            side = "SELL"
        else:
            pnl_pct = (self.entry_price - close) / self.entry_price
            side = "BUY"

        exit_reason = None
        
        if pnl_pct >= self.take_profit_pct:
            self.logger.info(
                f"💰 TAKE PROFIT: +{pnl_pct*100:.2f}% "
                f"(Entry={self.entry_price:.2f}, Close={close:.2f})"
            )
            exit_reason = f"TP (+{pnl_pct*100:.2f}%)"
            await self.execute_trade(side, close)
            
        elif pnl_pct <= -self.stop_loss_pct:
            self.logger.warning(
                f"🛑 STOP LOSS: {pnl_pct*100:.2f}% "
                f"(Entry={self.entry_price:.2f}, Close={close:.2f})"
            )
            exit_reason = f"SL ({pnl_pct*100:.2f}%)"
            await self.execute_trade(side, close)

        if exit_reason:
            self.last_exit_time = current_time
            self.logger.info(f"❄️ Cooldown started for {self.cooldown_minutes} mins ({exit_reason})")
            if self.risk_monitor:
                await self.risk_monitor.release_trade_slot()

    async def execute_trade(self, side: str, price: float) -> None:
        """Execute a trade with dynamic position sizing."""
        try:
            entry_price = price
            stop_loss = entry_price * (1 - self.stop_loss_pct)
            position_qty = self.capital_manager.calculate_quantity(entry_price, stop_loss)
            
            if position_qty < 1:
                self.logger.warning(f"⚠️ Position size too small: {position_qty}")
                return
            
            self.logger.debug(f"📊 Calculated Position Size: {position_qty} shares")
            
            response = await order_executor.place_order(
                self.symbol, 
                self.token, 
                side, 
                position_qty, 
                price=0.0
            )
            
            if response and response.get("status") == "success":
                if side == "BUY":
                    self.position = position_qty
                    self.entry_price = price
                    self.logger.info(f"✅ POSITION UPDATED: LONG {position_qty} @ {price:.2f}")
                elif side == "SELL":
                    self.position = -position_qty
                    self.entry_price = price
                    self.logger.info(f"✅ POSITION UPDATED: SHORT {position_qty} @ {price:.2f}")
            else:
                self.logger.error(f"❌ Trade execution failed: {response}")
                
        except Exception as e:
            self.logger.error(f"❌ Trade execution exception: {e}")