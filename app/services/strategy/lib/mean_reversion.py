import logging
from datetime import datetime, timedelta
from typing import Optional

from app.core.executors import run_blocking
from app.services.oms.executor import order_executor
from app.services.risk.position_sizer import CapitalManager
from app.services.strategy.base import BaseStrategy
from app.services.strategy.indicators import calculate_bollinger_bands, calculate_rsi

logger = logging.getLogger("MeanReversionStrategy")


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion Strategy using Bollinger Bands.
    """

    def __init__(self, symbol: str, token: str, risk_monitor=None, capital_manager: Optional[CapitalManager] = None):
        super().__init__("MEAN_REVERSION", symbol, token, risk_monitor)

        self.capital_manager = capital_manager or CapitalManager(total_capital=100000.0, risk_per_trade_pct=0.01)

        self.bb_period = 20
        self.bb_std_dev = 2.0
        self.rsi_period = 14

        self.stop_loss_pct = 0.0035
        self.take_profit_pct = 0.0060

        self.cooldown_minutes = 8
        self.last_exit_time: Optional[datetime] = None

    async def on_candle_close(self, candle: dict) -> None:
        if len(self.candles) < self.bb_period:
            return

        current_time = candle.get("start_time", datetime.now())

        # Cooldown Check
        if self.last_exit_time:
            if (current_time - self.last_exit_time) < timedelta(minutes=self.cooldown_minutes):
                return

        # ✅ FIX: Use standalone functions + Safe List Copy
        safe_candles = list(self.candles)

        rsi = await run_blocking(calculate_rsi, safe_candles, self.rsi_period)
        bb_upper, bb_lower = await run_blocking(
            calculate_bollinger_bands, safe_candles, self.bb_period, self.bb_std_dev
        )

        close = candle["close"]

        if self.position == 0:
            await self._check_entry_signals(close, rsi, bb_upper, bb_lower, current_time)
        elif self.position != 0:
            await self._check_exit_signals(close, current_time)

    async def _check_entry_signals(self, close, rsi, bb_upper, bb_lower, current_time):
        # Long Signal (Oversold)
        if close < bb_lower and rsi < 30:
            self.logger.info(f"🚀 MEAN REVERSION BUY @ {close:.2f} (BB_Low={bb_lower:.2f}, RSI={rsi:.1f})")
            await self.execute_trade("BUY", close)

        # Short Signal (Overbought)
        elif close > bb_upper and rsi > 70:
            self.logger.info(f"🔻 MEAN REVERSION SELL @ {close:.2f} (BB_High={bb_upper:.2f}, RSI={rsi:.1f})")
            await self.execute_trade("SELL", close)

    async def _check_exit_signals(self, close, current_time):
        if self.position > 0:
            pnl_pct = (close - self.entry_price) / self.entry_price
            side = "SELL"
        else:
            pnl_pct = (self.entry_price - close) / self.entry_price
            side = "BUY"

        exit_reason = None
        if pnl_pct >= self.take_profit_pct:
            exit_reason = "TP"
        elif pnl_pct <= -self.stop_loss_pct:
            exit_reason = "SL"

        if exit_reason:
            self.logger.info(f"Exit {exit_reason}: {pnl_pct*100:.2f}%")
            self.last_exit_time = current_time
            if self.risk_monitor:
                await self.risk_monitor.release_trade_slot()
            await self.execute_trade(side, close)

    async def execute_trade(self, side: str, price: float) -> None:
        """Executes trade with Position Sizing."""
        try:
            entry_price = price
            # Calculate SL Price for sizing
            if side == "BUY":
                stop_loss_price = entry_price * (1 - self.stop_loss_pct)
            else:
                stop_loss_price = entry_price * (1 + self.stop_loss_pct)

            qty = self.capital_manager.calculate_quantity(entry_price, stop_loss_price)

            if qty < 1:
                return

            response = await order_executor.place_order(self.symbol, self.token, side, qty, 0.0)

            if response and response.get("status") == "success":
                self.position = qty if side == "BUY" else -qty
                self.entry_price = price
                self.logger.info(f"✅ POSITION: {side} {qty} @ {price}")
        except Exception as e:
            self.logger.error(f"Execution Error: {e}")
