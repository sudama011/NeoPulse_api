import logging
from datetime import datetime, time, timedelta
from typing import Optional

from app.core.executors import run_blocking
from app.services.oms.executor import order_executor
from app.services.risk.position_sizer import CapitalManager
from app.services.strategy.base import BaseStrategy

logger = logging.getLogger("ORBStrategy")


class ORBStrategy(BaseStrategy):
    """
    Opening Range Breakout (ORB) Strategy.

    Logic:
    - Establishes opening range in first 15 minutes
    - LONG: Break above opening range high + 0.3%
    - SHORT: Break below opening range low - 0.3%

    Risk Management:
    - Take Profit: +0.7%
    - Stop Loss: -0.4%
    - Cooldown: 12 mins after exit
    - Only trades between 9:15 AM - 3:15 PM IST

    State Machine:
    1. SETUP (9:15-9:30) → Establish opening range
    2. FLAT (position == 0) → Waits for breakout
    3. LONG/SHORT → Exits on SL/TP
    4. COOLING → Prevents re-entry
    """

    def __init__(self, symbol: str, token: str, risk_monitor=None, capital_manager: Optional[CapitalManager] = None):
        super().__init__("OPENING_RANGE_BREAKOUT", symbol, token, risk_monitor)

        # Capital Management
        self.capital_manager = capital_manager or CapitalManager(total_capital=100000.0, risk_per_trade_pct=0.01)

        # Opening Range Configuration
        self.range_minutes = 15  # First 15 minutes
        self.breakout_threshold = 0.003  # 0.3% beyond range

        # Risk Configuration
        self.stop_loss_pct = 0.0040  # 0.4% Risk
        self.take_profit_pct = 0.0070  # 0.7% Reward
        self.position_qty = 18  # Default qty

        # Opening Range State
        self.range_open = 0.0
        self.range_high = 0.0
        self.range_low = float("inf")
        self.range_established = False
        self.range_start_time: Optional[datetime] = None

        # Cooldown Configuration
        self.cooldown_minutes = 12
        self.last_exit_time: Optional[datetime] = None

        # Trading State
        self.last_signal: Optional[str] = None
        self.last_signal_time: Optional[datetime] = None

    async def on_candle_close(self, candle: dict) -> None:
        """Processes closed 1-min candle."""
        current_time = candle["start_time"]
        close = candle["close"]

        # Only trade during market hours (9:15 AM - 3:15 PM IST)
        if not (time(9, 15) <= current_time.time() <= time(15, 15)):
            return

        # Phase 1: Establish Opening Range (first 15 mins)
        if not self.range_established:
            if not self.range_start_time:
                self.range_start_time = current_time
                self.range_open = close
                self.range_high = close
                self.range_low = close
                self.logger.info(f"📍 Opening Range Started @ {close:.2f}")
                return

            # Update range
            time_diff = current_time - self.range_start_time
            if time_diff.total_seconds() < self.range_minutes * 60:
                self.range_high = max(self.range_high, close)
                self.range_low = min(self.range_low, close)
                self.logger.debug(f"📊 Range Update: H={self.range_high:.2f}, L={self.range_low:.2f}")
                return
            else:
                # Range established
                self.range_established = True
                self.logger.info(
                    f"✅ Opening Range Established: "
                    f"H={self.range_high:.2f}, L={self.range_low:.2f}, Width={self.range_high - self.range_low:.2f}"
                )
                return

        # Phase 2: Check for breakouts
        if self.range_established:
            # Check Cooldown
            if self.last_exit_time:
                time_diff = current_time - self.last_exit_time
                if time_diff < timedelta(minutes=self.cooldown_minutes):
                    return

            # Entry Logic (only if FLAT)
            if self.position == 0:
                await self._check_entry_signals(close, current_time)
            # Exit Logic
            elif self.position != 0:
                await self._check_exit_signals(close, current_time)

    async def _check_entry_signals(self, close: float, current_time) -> None:
        """Check for breakout signals."""
        breakout_high = self.range_high * (1 + self.breakout_threshold)
        breakout_low = self.range_low * (1 - self.breakout_threshold)

        # Long Signal (breakout above range)
        if close > breakout_high:
            self.logger.info(f"🚀 ORB BREAKOUT UP @ {close:.2f} " f"(Range High={self.range_high:.2f})")
            self.last_signal = "BUY"
            self.last_signal_time = current_time
            await self.execute_trade("BUY", close)

        # Short Signal (breakout below range)
        elif close < breakout_low:
            self.logger.info(f"🔻 ORB BREAKOUT DOWN @ {close:.2f} " f"(Range Low={self.range_low:.2f})")
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
