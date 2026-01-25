import logging
import asyncio
from typing import Dict, Optional

logger = logging.getLogger("RiskMonitor")

class RiskMonitor:
    """
    Thread-safe Risk Management Monitor.
    
    Ensures:
    - Max daily loss limit is enforced
    - Concurrent trade count is bounded
    - All state updates are atomic via asyncio.Lock
    
    This is critical for algorithmic trading to prevent catastrophic losses.
    """

    def __init__(self):
        self.max_daily_loss = 1000.0
        self.max_concurrent_trades = 3
        
        # Daily State
        self.current_pnl = 0.0
        self.open_positions_count = 0
        self.daily_trade_count = 0
        
        # ✅ CRITICAL: Async lock for atomicity
        # Protects: open_positions_count, current_pnl reads/writes
        self._lock = asyncio.Lock()

    async def request_trade_slot(self) -> bool:
        """
        Atomically checks limits and reserves a trade slot.
        MUST be called BEFORE placing an order.
        
        Returns:
            bool: True if trade slot was reserved, False if limits breached
        """
        async with self._lock:
            # 1. Check PnL (Stop if we lost too much)
            if self.current_pnl <= -(self.max_daily_loss):
                logger.warning(
                    f"🛑 Max Daily Loss Hit: Current PnL {self.current_pnl:.2f} "
                    f"<= -{self.max_daily_loss:.2f}"
                )
                return False
                
            # 2. Check Trade Count
            if self.open_positions_count >= self.max_concurrent_trades:
                logger.warning(
                    f"🛑 Max Daily Trades Hit: {self.open_positions_count}/{self.max_concurrent_trades}"
                )
                return False
                
            # 3. Increment (Reserve Slot) - NOW ATOMIC
            self.open_positions_count += 1
            self.daily_trade_count += 1
            logger.debug(f"✅ Trade slot reserved: {self.open_positions_count}/{self.max_concurrent_trades}")
            return True

    async def rollback_trade_slot(self) -> None:
        """
        Reverses the slot reservation if the order failed execution.
        
        Call this in the exception handler of place_order() if execution fails
        after request_trade_slot() succeeded.
        """
        async with self._lock:
            if self.open_positions_count > 0:
                self.open_positions_count -= 1
                self.daily_trade_count -= 1
                logger.info("⏪ Trade slot rolled back due to execution failure.")

    async def release_trade_slot(self) -> None:
        """
        Releases a trade slot after a position is successfully closed.
        
        Call this AFTER a position is closed (e.g., via Take Profit or Stop Loss).
        This decrements the open_positions_count counter to free up a slot for new trades.
        """
        async with self._lock:
            if self.open_positions_count > 0:
                self.open_positions_count -= 1
                logger.info(f"✅ Trade slot released: {self.open_positions_count}/{self.max_concurrent_trades}")

    async def update_pnl(self, realized_pnl: float) -> None:
        """
        Atomically updates realized PnL.
        
        Args:
            realized_pnl: Positive (profit) or negative (loss) PnL
        """
        async with self._lock:
            self.current_pnl += realized_pnl
            logger.debug(f"💹 PnL Updated: {self.current_pnl:.2f} (Change: {realized_pnl:+.2f})")

    async def reset_daily_stats(self) -> None:
        """Reset risk monitor for a new trading day."""
        async with self._lock:
            self.open_positions_count = 0
            self.current_pnl = 0.0
            logger.info("♻️ Risk Monitor Stats Reset for New Day")

    async def update_config(
        self, 
        max_daily_loss: float, 
        max_concurrent_trades: int, 
        risk_params: Optional[Dict] = None
    ) -> None:
        """
        Updates risk limits dynamically from API request.
        
        Args:
            max_daily_loss: Maximum cumulative loss allowed per day
            max_concurrent_trades: Maximum number of open trades
            risk_params: Optional additional risk parameters (for future use)
        """
        async with self._lock:
            self.max_daily_loss = max_daily_loss
            self.max_concurrent_trades = max_concurrent_trades
            
            logger.info(
                f"🛡️ Risk Config Updated: "
                f"Max Loss={self.max_daily_loss:.2f}, "
                f"Max Trades={self.max_concurrent_trades}"
            )

    async def get_status(self) -> Dict:
        """
        Returns current risk monitor status (thread-safe read).
        
        Returns:
            dict: Current state snapshot
        """
        async with self._lock:
            return {
                "current_pnl": self.current_pnl,
                "open_positions_count": self.open_positions_count,
                "daily_trade_count": self.daily_trade_count,
                "max_daily_loss": self.max_daily_loss,
                "max_concurrent_trades": self.max_concurrent_trades,
                "loss_percentage": (self.current_pnl / -self.max_daily_loss * 100) 
                                   if self.max_daily_loss != 0 else 0,
            }


# Global Singleton
risk_monitor = RiskMonitor()