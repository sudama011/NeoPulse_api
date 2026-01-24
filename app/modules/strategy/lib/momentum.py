from app.modules.strategy.base import BaseStrategy
from app.modules.strategy.indicators import calculate_rsi, calculate_vwap
import logging

class MomentumStrategy(BaseStrategy):
    def __init__(self, symbol: str, token: str):
        super().__init__("MOMENTUM_RSI", symbol, token)
        self.rsi_period = 14
        self.stop_loss_pct = 0.0025 # 0.25%
        self.take_profit_pct = 0.0050 # 0.5%

    async def on_candle_close(self, candle: dict):
        """
        Runs every minute when a candle closes.
        """
        # 1. Need enough data
        if len(self.candles) < 20:
            self.logger.info(f"⏳ Building History: {len(self.candles)}/20")
            return

        # 2. Calculate Indicators
        rsi = calculate_rsi(self.candles, self.rsi_period)
        vwap = calculate_vwap(self.candles)
        close = candle['close']

        self.logger.info(f"📊 Close: {close} | RSI: {rsi:.2f} | VWAP: {vwap:.2f}")

        # 3. Strategy Logic (Entry)
        if self.position == 0:
            # --- LONG CONDITION ---
            # Close > VWAP AND RSI > 60
            if close > vwap and rsi > 60:
                self.logger.info("🚀 BUY SIGNAL: Momentum Breakout")
                await self.execute_trade("BUY", close)

            # --- SHORT CONDITION ---
            # Close < VWAP AND RSI < 40
            elif close < vwap and rsi < 40:
                self.logger.info("🔻 SELL SIGNAL: Momentum Breakdown")
                await self.execute_trade("SELL", close)

        # 4. Strategy Logic (Exit)
        elif self.position != 0:
            await self.check_exit(close)

    async def execute_trade(self, side, price):
        # Placeholder for OMS (Phase 4)
        self.position = 1 if side == "BUY" else -1
        self.entry_price = price
        self.logger.info(f"✅ EXECUTE {side} @ {price}")

    async def check_exit(self, current_price):
        if self.position == 0: 
            return

        # Long Exit Logic
        if self.position == 1:
            pnl_pct = (current_price - self.entry_price) / self.entry_price
            if pnl_pct >= self.take_profit_pct:
                self.logger.info(f"💰 TAKE PROFIT (+{pnl_pct*100:.2f}%)")
                self.position = 0
            elif pnl_pct <= -self.stop_loss_pct:
                self.logger.info(f"🛑 STOP LOSS ({pnl_pct*100:.2f}%)")
                self.position = 0