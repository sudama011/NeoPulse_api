import asyncio
import logging
from typing import Dict, Optional

from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models.orders import OrderLedger

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

    async def _get_actual_open_positions_count(self) -> int:
        """
        Queries the database to get the ACTUAL count of open positions.

        This prevents the "restart bug" where in-memory counter resets to 0
        but actual positions still exist in the database.

        Returns:
            int: Number of currently open positions from database
        """
        try:
            async with AsyncSessionLocal() as session:
                # Count distinct tokens with COMPLETE status (filled orders)
                # We need to calculate net position per token
                # A position is "open" if net quantity != 0

                # Query: Get all COMPLETE orders, group by token, sum quantities
                query = (
                    select(
                        OrderLedger.token,
                        func.sum(
                            func.case(
                                (OrderLedger.transaction_type == "BUY", OrderLedger.quantity),
                                else_=-OrderLedger.quantity,
                            )
                        ).label("net_qty"),
                    )
                    .where(OrderLedger.status == "COMPLETE")
                    .group_by(OrderLedger.token)
                )

                result = await session.execute(query)
                positions = result.all()

                # Count positions where net_qty != 0
                open_count = sum(1 for _, net_qty in positions if net_qty != 0)

                return open_count

        except Exception as e:
            logger.error(f"❌ Failed to query database for open positions: {e}")
            # Fallback to in-memory counter if DB query fails
            return self.open_positions_count

    async def request_trade_slot(self) -> bool:
        """
        Atomically checks limits and reserves a trade slot.
        MUST be called BEFORE placing an order.

        ✅ CRITICAL FIX: Now verifies against DATABASE to prevent restart bug.

        Returns:
            bool: True if trade slot was reserved, False if limits breached
        """
        async with self._lock:
            # 1. Check PnL (Stop if we lost too much)
            if self.current_pnl <= -(self.max_daily_loss):
                logger.warning(
                    f"🛑 Max Daily Loss Hit: Current PnL {self.current_pnl:.2f} " f"<= -{self.max_daily_loss:.2f}"
                )
                return False

            # 2. ✅ CRITICAL: Verify actual open positions from DATABASE
            actual_open_count = await self._get_actual_open_positions_count()

            # Sync in-memory counter with database reality
            if actual_open_count != self.open_positions_count:
                logger.warning(
                    f"⚠️ Position count mismatch detected! "
                    f"Memory: {self.open_positions_count}, Database: {actual_open_count}. "
                    f"Syncing to database value..."
                )
                self.open_positions_count = actual_open_count

            # 3. Check Trade Count (using synced value)
            if self.open_positions_count >= self.max_concurrent_trades:
                logger.warning(
                    f"🛑 Max Concurrent Trades Hit: {self.open_positions_count}/{self.max_concurrent_trades}"
                )
                return False

            # 4. Increment (Reserve Slot) - NOW ATOMIC AND DB-VERIFIED
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
        """
        Reset risk monitor for a new trading day.

        ✅ CRITICAL FIX: Now syncs with database to get actual open positions.
        """
        async with self._lock:
            # Sync with database to get actual open positions
            actual_open_count = await self._get_actual_open_positions_count()
            self.open_positions_count = actual_open_count
            self.current_pnl = 0.0

            if actual_open_count > 0:
                logger.warning(f"♻️ Risk Monitor Reset: Found {actual_open_count} open positions from previous session")
            else:
                logger.info("♻️ Risk Monitor Stats Reset for New Day")

    async def sync_with_database(self) -> None:
        """
        Syncs in-memory position count with database reality.

        Should be called on bot startup to recover from crashes.
        """
        async with self._lock:
            actual_open_count = await self._get_actual_open_positions_count()

            if actual_open_count != self.open_positions_count:
                logger.warning(
                    f"🔄 Syncing position count: Memory={self.open_positions_count}, " f"Database={actual_open_count}"
                )
                self.open_positions_count = actual_open_count
            else:
                logger.info(f"✅ Position count in sync: {self.open_positions_count} open positions")

    async def update_config(
        self, max_daily_loss: float, max_concurrent_trades: int, risk_params: Optional[Dict] = None
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
                "loss_percentage": (self.current_pnl / -self.max_daily_loss * 100) if self.max_daily_loss != 0 else 0,
            }


# Global Singleton
risk_monitor = RiskMonitor()
