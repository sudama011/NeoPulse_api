import logging
from datetime import datetime, timedelta
from typing import Optional

from app.core.executors import run_blocking
from app.services.oms.executor import order_executor
from app.services.risk.position_sizer import CapitalManager
from app.services.strategy.base import BaseStrategy
from app.services.strategy.indicators import calculate_ema, calculate_rsi, calculate_vwap

logger = logging.getLogger("GapFillStrategy")


class GapFillStrategy(BaseStrategy):
    """
    Gap Fill Strategy - Trades mean reversion on intraday gaps.

    Logic:
    - LONG: Price < Previous Close AND Price < SMA(20)
    - SHORT: Price > Previous Close AND Price > SMA(20)

    Risk Management:
    - Take Profit: +0.5%
    - Stop Loss: -0.4%
    - Cooldown: 5 mins after exit

    State Machine:
    1. FLAT (position == 0) → Enters on signal
    2. LONG (position > 0) → Exits on SL/TP
    3. SHORT (position < 0) → Exits on SL/TP
    4. COOLING → Waits before re-entry
    """

    def __init__(self, symbol: str, token: str, risk_monitor=None, capital_manager: Optional[CapitalManager] = None):
        super().__init__("GAP_FILL", symbol, token, risk_monitor)

        # Capital Management (Position Sizing)
        self.capital_manager = capital_manager or CapitalManager(total_capital=100000.0, risk_per_trade_pct=0.01)

        # Indicator Configuration
        self.sma_period = 20

        # Risk Configuration
        self.stop_loss_pct = 0.0040  # 0.4% Risk
        self.take_profit_pct = 0.0050  # 0.5% Reward
        self.position_qty = 20  # Default qty

        # Cooldown Configuration
        self.cooldown_minutes = 5
        self.last_exit_time: Optional[datetime] = None
        self.prev_close = 0.0

        # Trading State
        self.last_signal: Optional[str] = None
        self.last_signal_time: Optional[datetime] = None

    async def on_candle_close(self, candle: dict) -> None:
        """
        Processes closed 1-min candle.
        """
        # Check Data Quality
        if len(self.candles) < self.sma_period + 1:
            return

        current_time = candle["start_time"]

        # Check Cooldown
        if self.last_exit_time:
            time_diff = current_time - self.last_exit_time
            if time_diff < timedelta(minutes=self.cooldown_minutes):
                return

        # Calculate SMA (Thread-safe)
        sma = await run_blocking(self._calculate_sma, self.candles, self.sma_period)
        close = candle["close"]

        # Entry Logic
        if self.position == 0:
            await self._check_entry_signals(close, sma, current_time)
        # Exit Logic
        elif self.position != 0:
            await self._check_exit_signals(close, current_time)

    def _calculate_sma(self, candles: list, period: int) -> float:
        """Calculate Simple Moving Average (runs in thread pool)."""
        if len(candles) < period:
            return 0.0
        closes = [c["close"] for c in candles[-period:]]
        return sum(closes) / len(closes)

    async def _check_entry_signals(self, close: float, sma: float, current_time) -> None:
        """
        Entry signals based on gap fill logic.

        LONG: Price < Previous Close AND Price < SMA
        SHORT: Price > Previous Close AND Price > SMA
        """
        if not self.candles or len(self.candles) < 2:
            return

        prev_close = self.candles[-2]["close"] if len(self.candles) >= 2 else 0.0

        # Long Signal (filling gap down)
        if close < prev_close and close < sma:
            self.logger.info(f"🚀 GAP FILL BUY @ {close:.2f} " f"(PrevClose={prev_close:.2f}, SMA={sma:.2f})")
            self.last_signal = "BUY"
            self.last_signal_time = current_time
            await self.execute_trade("BUY", close)

        # Short Signal (filling gap up)
        elif close > prev_close and close > sma:
            self.logger.info(f"🔻 GAP FILL SELL @ {close:.2f} " f"(PrevClose={prev_close:.2f}, SMA={sma:.2f})")
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
                f"💰 TAKE PROFIT: +{pnl_pct*100:.2f}% " f"(Entry={self.entry_price:.2f}, Close={close:.2f})"
            )
            exit_reason = f"TP (+{pnl_pct*100:.2f}%)"
            await self.execute_trade(side, close)

        elif pnl_pct <= -self.stop_loss_pct:
            self.logger.warning(
                f"🛑 STOP LOSS: {pnl_pct*100:.2f}% " f"(Entry={self.entry_price:.2f}, Close={close:.2f})"
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

            response = await order_executor.place_order(self.symbol, self.token, side, position_qty, price=0.0)

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
