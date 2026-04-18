import logging
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.execution.engine import execution_engine
from app.execution.kotak import kotak_adapter
from app.risk.manager import risk_manager
from app.schemas.execution import OrderResponse, OrderStatus

logger = logging.getLogger("StrategyBase")


class BaseStrategy(ABC):
    """
    The robust base class for all strategies.

    Features:
    - Auto-Reconciliation: Syncs with Broker on startup.
    - Smart Risk Checks: Auto-detects Entry vs. Exit to prevent blocking critical stops.
    - Crash Protection: Catches logic errors to keep the engine running.
    - Dual-Mode: Works with both live ticks (on_tick) and backtest candles (on_candle).
    - Candle Buffer: Maintains rolling history for indicator calculations.
    """

    # Default warmup period — subclasses can override
    WARMUP_PERIOD: int = 50

    def __init__(self, name: str, symbol: str, token: str, params: Dict[str, Any] = None, leverage: float = 1.0):
        self.name = name
        self.symbol = symbol
        self.token = str(token)
        self.params = params or {}
        self.leverage = leverage  # Strategy-specific leverage

        # Internal State
        self.position = 0  # Net Quantity (+ve Long, -ve Short)
        self.avg_price = 0.0
        self.is_active = True
        self.last_trade_time: Optional[datetime] = None

        # Candle Buffer (for indicator calculations in both modes)
        self._max_history = params.get("max_history", 500) if params else 500
        self.candles: deque = deque(maxlen=self._max_history)

        # Crash Protection
        self._error_count = 0
        self._max_errors_before_stop = 5

        # Backtest mode flag (set by backtest engine)
        self._backtest_mode = False

    async def initialize(self):
        """Lifecycle hook: Called by Engine before ticks start."""
        await self.sync_position()

    async def sync_position(self):
        """
        CRITICAL: Fetches actual open position from Broker to prevent State Drift.
        """
        if not kotak_adapter.is_logged_in:
            logger.warning(f"⚠️ {self.name}: Broker not logged in. Skipping Sync.")
            return

        try:
            response = await kotak_adapter.get_positions()
            data = response.get("data", [])

            if not data:
                return

            found = False
            for pos in data:
                # Match either Token or Symbol
                p_token = str(pos.get("instrumentToken", ""))
                p_symbol = pos.get("instrumentName", "")

                if p_token == self.token or p_symbol == self.symbol:
                    net_qty = int(pos.get("netQty", 0))

                    if net_qty != 0:
                        self.position = net_qty
                        self.avg_price = float(pos.get("avgPrice", 0.0))
                        logger.info(
                            f"🔄 {self.name}: RECONCILED! Found existing position: {self.position} @ {self.avg_price}"
                        )
                        found = True
                    break

            if not found:
                self.position = 0

        except Exception as e:
            logger.error(f"❌ {self.name}: Sync Failed: {e}")

    @abstractmethod
    async def on_tick(self, tick: Dict[str, Any]):
        """Core Logic: Must be implemented by child strategy."""
        pass

    async def on_candle(self, candle: Dict[str, Any]):
        """
        Called with a complete OHLCV candle (backtest or candle-aggregated live).
        Default: appends to history and delegates to on_tick with LTP = close.
        Override this in subclasses for candle-based logic.
        """
        self.candles.append(candle)

        # Convert candle to a tick-like dict so on_tick works
        tick = {
            "ltp": candle["close"],
            "volume": candle.get("volume", 0),
            "_ohlc": {"high": candle["high"], "low": candle["low"]},
            "_candle": candle,
        }
        await self.on_tick(tick)

    async def safe_on_tick(self, tick: Dict[str, Any]):
        """Wrapper to prevent one bad math operation from killing the strategy loop."""
        try:
            await self.on_tick(tick)
            self._error_count = 0  # Reset on success
        except Exception as e:
            self._error_count += 1
            logger.error(f"🔥 {self.name} LOGIC ERROR: {e}", exc_info=True)

            if self._error_count >= self._max_errors_before_stop:
                logger.critical(f"⛔ {self.name}: Too many errors ({self._error_count}). Disabling Strategy.")
                self.is_active = False

    async def safe_on_candle(self, candle: Dict[str, Any]):
        """Protected candle handler for backtest engine."""
        try:
            await self.on_candle(candle)
            self._error_count = 0
        except Exception as e:
            self._error_count += 1
            logger.error(f"🔥 {self.name} CANDLE ERROR: {e}", exc_info=True)

            if self._error_count >= self._max_errors_before_stop:
                logger.critical(f"⛔ {self.name}: Too many errors ({self._error_count}). Disabling Strategy.")
                self.is_active = False

    async def buy(
        self, price: float, sl: float = 0.0, qty: int = None, confidence: float = 1.0, tag: str = "SIGNAL"
    ) -> Optional[OrderResponse]:
        """
        Smart Buy Wrapper (Handles LONG ENTRY or SHORT EXIT).
        """
        # 1. Determine Intent & Quantity
        is_entry = False
        calculated_qty = 0

        if self.position < 0:
            # Case A: We are Short. Buying means COVERING (Exit).
            # Default to full cover if qty not specified.
            calculated_qty = qty if qty is not None else abs(self.position)
            tag = tag or "COVER_SHORT"
            is_entry = False
        else:
            # Case B: We are Flat/Long. Buying means ENTERING/ADDING (Entry).
            if qty is not None:
                calculated_qty = qty
            else:
                # Ask Risk Manager for Size (with strategy-specific leverage)
                calculated_qty = await risk_manager.calculate_size(
                    self.symbol, entry=price, sl=sl, confidence=confidence, leverage=self.leverage
                )
            is_entry = True

        if calculated_qty <= 0:
            return None

        # 2. Risk Check (Only for Entries)
        if is_entry:
            # Calculate Risk Delta
            new_exposure = abs(self.position + calculated_qty)
            current_exposure = abs(self.position)

            if new_exposure > current_exposure:
                # We are increasing risk -> Strict Check
                allowed = True
                # allowed = await risk_manager.can_trade(self.symbol, calculated_qty, price)
                if not allowed:
                    logger.warning(f"⚠️ {self.name}: Risk Manager denied Long Entry.")
                    return None

        # 3. Execute
        logger.info(f"🤖 {self.name} BUY ({'ENTRY' if is_entry else 'EXIT'}): {calculated_qty} @ {price}")
        return await self._execute("BUY", calculated_qty, price, tag)

    async def sell(
        self, price: float, sl: float = 0.0, qty: int = None, confidence: float = 1.0, tag: str = "SIGNAL"
    ) -> Optional[OrderResponse]:
        """
        Smart Sell Wrapper (Handles LONG EXIT or SHORT ENTRY).
        """
        # 1. Determine Intent & Quantity
        is_entry = False
        calculated_qty = 0

        if self.position > 0:
            # Case A: We are Long. Selling means EXITING.
            calculated_qty = qty if qty is not None else abs(self.position)
            tag = tag or "EXIT_LONG"
            is_entry = False
        else:
            # Case B: We are Flat/Short. Selling means ENTERING SHORT.
            if qty is not None:
                calculated_qty = qty
            else:
                calculated_qty = await risk_manager.calculate_size(
                    self.symbol, entry=price, sl=sl, confidence=confidence, leverage=self.leverage
                )
            is_entry = True

        if calculated_qty <= 0:
            return None

        # 2. Risk Check (Only for Entries)
        if is_entry:
            new_exposure = abs(self.position - calculated_qty)
            current_exposure = abs(self.position)

            if new_exposure > current_exposure:
                allowed = await risk_manager.can_trade(self.symbol, calculated_qty, price)
                if not allowed:
                    logger.warning(f"⚠️ {self.name}: Risk Manager denied Short Entry.")
                    return None

        # 3. Execute
        logger.info(f"🤖 {self.name} SELL ({'ENTRY' if is_entry else 'EXIT'}): {calculated_qty} @ {price}")
        return await self._execute("SELL", calculated_qty, price, tag)

    async def _execute(self, side: str, qty: int, price: float, tag: str) -> Optional[OrderResponse]:
        """Internal helper to send order and update state."""

        # Debounce (1 sec)
        if self.last_trade_time and (datetime.now() - self.last_trade_time).seconds < 1:
            return None

        response = await execution_engine.execute_order(
            symbol=self.symbol, token=self.token, side=side, quantity=qty, price=price, tag=tag
        )

        if response and response.status in [OrderStatus.COMPLETE, OrderStatus.PARTIAL]:
            filled = response.filled_qty
            fill_price = response.average_price if response.average_price else price

            # Update position and average price
            old_position = self.position
            old_avg_price = self.avg_price

            if side == "BUY":
                new_position = old_position + filled

                # Calculate new average price
                if old_position >= 0:
                    # Adding to long or opening long
                    if new_position != 0:
                        self.avg_price = ((old_position * old_avg_price) + (filled * fill_price)) / new_position
                else:
                    # Reducing short position
                    if new_position >= 0:
                        # Closed short and possibly opened long
                        self.avg_price = fill_price if new_position > 0 else 0.0
                    else:
                        # Still short, avg price unchanged
                        pass

                self.position = new_position

            else:  # SELL
                new_position = old_position - filled

                # Calculate new average price
                if old_position <= 0:
                    # Adding to short or opening short
                    if new_position != 0:
                        self.avg_price = ((abs(old_position) * old_avg_price) + (filled * fill_price)) / abs(new_position)
                else:
                    # Reducing long position
                    if new_position <= 0:
                        # Closed long and possibly opened short
                        self.avg_price = fill_price if new_position < 0 else 0.0
                    else:
                        # Still long, avg price unchanged
                        pass

                self.position = new_position

            self.last_trade_time = datetime.now()
            logger.info(
                f"✅ {self.name} Position Updated: {old_position} → {self.position} | "
                f"Avg Price: {old_avg_price:.2f} → {self.avg_price:.2f}"
            )

        return response

    async def close_position(self, tag: str = "FORCE_CLOSE"):
        """Emergency Helper: Flattens any open position."""
        if self.position == 0:
            return

        if self.position > 0:
            await self._execute("SELL", self.position, 0.0, tag)
        else:
            await self._execute("BUY", abs(self.position), 0.0, tag)
