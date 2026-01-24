import logging
from datetime import timedelta
from app.modules.strategy.base import BaseStrategy
from app.modules.strategy.indicators import calculate_rsi, calculate_vwap, calculate_ema
from app.modules.oms.execution import order_executor

class MomentumStrategy(BaseStrategy):
    def __init__(self, symbol: str, token: str):
        super().__init__("MOMENTUM_TREND", symbol, token)
        self.rsi_period = 14
        self.ema_period = 50 
        
        # 🎯 OPTIMIZED SETTINGS
        self.stop_loss_pct = 0.0030     # 0.3% Risk
        self.take_profit_pct = 0.0090   # 0.9% Reward

        # ❄️ COOLDOWN SETTINGS
        self.cooldown_minutes = 10      # Increased to 30 mins
        self.last_exit_time = None      

    async def on_candle_close(self, candle: dict):
        # 1. Check Data Quality
        if len(self.candles) < self.ema_period:
            return

        # 2. ❄️ CHECK COOLDOWN (The "Anti-Flicker" Logic)
        current_time = candle['start_time']
        if self.last_exit_time:
            time_diff = current_time - self.last_exit_time
            if time_diff < timedelta(minutes=self.cooldown_minutes):
                # LOGGING THIS TO PROVE IT WORKS
                # self.logger.info(f"❄️ Cooling down... ({time_diff} elapsed)") 
                return

        # 3. Calculate Indicators
        rsi = calculate_rsi(self.candles, self.rsi_period)
        vwap = calculate_vwap(self.candles)
        ema = calculate_ema(self.candles, self.ema_period)
        close = candle['close']

        # 4. Entry Logic
        if self.position == 0:
            # Long: Price > EMA & RSI > 60 & Price > VWAP
            if close > ema and rsi > 60 and close > vwap:
                self.logger.info(f"🚀 BUY SIGNAL @ {close}")
                await self.execute_trade("BUY", close)

            # Short: Price < EMA & RSI < 40 & Price < VWAP
            elif close < ema and rsi < 40 and close < vwap:
                self.logger.info(f"🔻 SELL SIGNAL @ {close}")
                await self.execute_trade("SELL", close)

        # 5. Exit Logic
        elif self.position != 0:
            if self.position > 0: # Long
                pnl_pct = (close - self.entry_price) / self.entry_price
                side = "SELL"
            else: # Short
                pnl_pct = (self.entry_price - close) / self.entry_price
                side = "BUY"

            is_exit = False
            # Check Targets
            if pnl_pct >= self.take_profit_pct:
                self.logger.info(f"💰 TAKE PROFIT (+{pnl_pct*100:.2f}%)")
                is_exit = True
            elif pnl_pct <= -self.stop_loss_pct:
                self.logger.info(f"🛑 STOP LOSS ({pnl_pct*100:.2f}%)")
                is_exit = True

            if is_exit:
                await self.execute_trade(side, close)
                # 🕒 SET EXIT TIME
                self.last_exit_time = current_time 
                self.logger.info(f"❄️ Cooldown Started for {self.cooldown_minutes} mins")

    async def execute_trade(self, side, price):
        qty = 25
        await order_executor.place_order(self.symbol, self.token, side, qty, 0.0)

        # Sync State
        if side == "BUY" and self.position <= 0:
            self.position = qty
            self.entry_price = price
        elif side == "SELL" and self.position >= 0:
            self.position = -qty
            self.entry_price = price
        else:
            self.position = 0
            self.entry_price = 0.0