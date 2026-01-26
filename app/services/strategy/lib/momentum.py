import logging
from datetime import datetime, timedelta
from typing import Optional

from app.core.executors import run_blocking
from app.services.oms.executor import order_executor
from app.services.risk.position_sizer import CapitalManager
from app.services.strategy.base import BaseStrategy
from app.services.strategy.indicators import calculate_ema, calculate_rsi, calculate_vwap

logger = logging.getLogger("MomentumStrategy")


class MomentumStrategy(BaseStrategy):
    """
    Momentum Trend Following Strategy.

    Signals:
    - LONG: Price > EMA(50) AND RSI(14) > 60 AND Price > VWAP
    - SHORT: Price < EMA(50) AND RSI(14) < 40 AND Price < VWAP

    Risk Management:
    - Take Profit: +0.9%
    - Stop Loss: -0.3%
    - Cooldown: 10 mins after exit to prevent whipsaws

    State Machine:
    1. FLAT (position == 0) → Enters on signal
    2. LONG (position > 0) → Exits on SL/TP
    3. SHORT (position < 0) → Exits on SL/TP
    4. COOLING → Waits before re-entry
    """

    def __init__(self, symbol: str, token: str, risk_monitor=None, capital_manager: Optional[CapitalManager] = None):
        super().__init__("MOMENTUM_TREND", symbol, token, risk_monitor)

        # Capital Management (Position Sizing)
        self.capital_manager = capital_manager or CapitalManager(total_capital=100000.0, risk_per_trade_pct=0.01)

        # Indicator Configuration
        self.rsi_period = 14
        self.ema_period = 50

        # Risk Configuration
        self.stop_loss_pct = 0.0030  # 0.3% Risk
        self.take_profit_pct = 0.0090  # 0.9% Reward
        self.position_qty = 25  # Default qty (will be overridden by capital_manager)

        # Cooldown Configuration (Anti-Whipsaw)
        self.cooldown_minutes = 10
        self.last_exit_time: Optional[datetime] = None

        # Trading State (for debugging)
        self.last_signal: Optional[str] = None
        self.last_signal_time: Optional[datetime] = None

    async def on_candle_close(self, candle: dict) -> None:
        """
        Processes closed 1-min candle.

        Flow:
        1. Check data quality (enough candles for indicators)
        2. Check cooldown (prevent rapid re-entries)
        3. Calculate indicators (in thread pool to avoid blocking event loop)
        4. Entry logic (only if FLAT)
        5. Exit logic (only if in position)
        """
        # 1. Check Data Quality
        if len(self.candles) < self.ema_period:
            return

        current_time = candle["start_time"]

        # 2. Check Cooldown (Anti-Flicker Logic)
        # If we exited recently, don't re-enter until cooldown expires
        if self.last_exit_time:
            time_diff = current_time - self.last_exit_time
            if time_diff < timedelta(minutes=self.cooldown_minutes):
                # Cooling down - skip signal generation
                return

        # 3. Calculate Indicators (Thread-safe: heavy pandas operations run in executor)
        rsi = await run_blocking(calculate_rsi, list(self.candles), self.rsi_period)
        vwap = await run_blocking(calculate_vwap, list(self.candles))
        ema = await run_blocking(calculate_ema, list(self.candles), self.ema_period)
        close = candle["close"]

        # 4. Entry Logic (Only when FLAT)
        if self.position == 0:
            await self._check_entry_signals(close, ema, rsi, vwap, current_time)

        # 5. Exit Logic (Only when in position)
        elif self.position != 0:
            await self._check_exit_signals(close, current_time)

    async def _check_entry_signals(self, close: float, ema: float, rsi: float, vwap: float, current_time) -> None:
        """
        Checks for entry signals based on technical criteria.

        LONG Signal: Price > EMA AND RSI > 60 AND Price > VWAP
        SHORT Signal: Price < EMA AND RSI < 40 AND Price < VWAP
        """
        # Long Signal
        if close > ema and rsi > 60 and close > vwap:
            self.logger.info(f"🚀 BUY SIGNAL @ {close:.2f} " f"(EMA={ema:.2f}, RSI={rsi:.1f}, VWAP={vwap:.2f})")
            self.last_signal = "BUY"
            self.last_signal_time = current_time
            await self.execute_trade("BUY", close)

        # Short Signal
        elif close < ema and rsi < 40 and close < vwap:
            self.logger.info(f"🔻 SELL SIGNAL @ {close:.2f} " f"(EMA={ema:.2f}, RSI={rsi:.1f}, VWAP={vwap:.2f})")
            self.last_signal = "SELL"
            self.last_signal_time = current_time
            await self.execute_trade("SELL", close)

    async def _check_exit_signals(self, close: float, current_time) -> None:
        """
        Checks for exit signals based on PnL targets.

        For LONG: Exit on TP (+0.9%) or SL (-0.3%)
        For SHORT: Exit on TP (+0.9%) or SL (-0.3%)
        """
        if self.position > 0:
            # LONG position
            pnl_pct = (close - self.entry_price) / self.entry_price
            side = "SELL"
        else:
            # SHORT position
            pnl_pct = (self.entry_price - close) / self.entry_price
            side = "BUY"

        exit_reason = None

        # Check Targets
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

        # If we exited, start cooldown and release trade slot
        if exit_reason:
            self.last_exit_time = current_time
            self.logger.info(f"❄️ Cooldown started for {self.cooldown_minutes} mins ({exit_reason})")
            # Release the trade slot back to the risk monitor
            if self.risk_monitor:
                await self.risk_monitor.release_trade_slot()

    async def execute_trade(self, side: str, price: float) -> None:
        """
        Executes a trade (BUY or SELL).

        CRITICAL: State update happens AFTER broker confirmation (delayed).
        This is intentional to avoid divergence if order fails.

        Flow:
        1. Calculate position size using CapitalManager
        2. Send order to broker
        3. Broker confirms or rejects
        4. Update internal position only if confirmed

        Note: In real implementation, you should use order confirmation
        from on_order_update() callback rather than updating here.
        """
        try:
            # 1. Calculate position quantity using stop-loss based position sizing
            entry_price = price
            stop_loss = entry_price * (1 - self.stop_loss_pct)
            position_qty = self.capital_manager.calculate_quantity(entry_price, stop_loss)

            if position_qty < 1:
                self.logger.warning(
                    f"⚠️ Position size calculation returned 0. " f"Entry: {entry_price:.2f}, SL: {stop_loss:.2f}"
                )
                return

            self.logger.debug(f"📊 Calculated Position Size: {position_qty} shares")

            # 2. Send order to broker
            response = await order_executor.place_order(
                self.symbol, self.token, side, position_qty, price=0.0  # Market order
            )

            # 3. Only update state if order was accepted by broker
            if response and response.get("status") == "success":
                if side == "BUY":
                    # Entering LONG or exiting SHORT
                    self.position = position_qty
                    self.entry_price = price
                    self.logger.info(f"✅ POSITION UPDATED: LONG {position_qty} @ {price:.2f}")

                elif side == "SELL":
                    # Exiting LONG or entering SHORT
                    self.position = -position_qty
                    self.entry_price = price
                    self.logger.info(f"✅ POSITION UPDATED: SHORT {position_qty} @ {price:.2f}")
            else:
                self.logger.error(f"❌ Trade execution failed: {response}")

        except Exception as e:
            self.logger.error(f"❌ Trade execution exception: {e}")
            # Position NOT updated on failure (safe!)
