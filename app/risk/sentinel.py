import asyncio
import logging
from datetime import datetime

from app.execution.kotak import kotak_adapter
from app.schemas.common import RiskConfig

logger = logging.getLogger("RiskSentinel")


class RiskSentinel:
    """
    Enforces risk limits by synchronizing with Broker State.
    """

    def __init__(self, config: RiskConfig):
        self.config = config

        # State
        self.current_pnl = 0.0
        self.open_trades = 0
        self.trades_today = 0

        # Concurrency
        self._lock = asyncio.Lock()
        self.last_sync_time = None

    async def update_config(self, max_daily_loss: float, max_concurrent_trades: int):
        """Dynamic config update from API/DB"""
        async with self._lock:
            self.config.max_daily_loss = max_daily_loss
            self.config.max_concurrent_trades = max_concurrent_trades
            logger.info(f"🛡️ Risk Config Updated: Max Loss {max_daily_loss}, Max Trades {max_concurrent_trades}")

    async def sync_state(self):
        """
        CRITICAL: Reconstructs state from Broker.
        """
        async with self._lock:
            logger.info("♻️ Risk Sentinel: Syncing with Broker...")

            if not kotak_adapter.is_logged_in:
                logger.warning("⚠️ Broker not logged in. Risk State might be stale.")
                return

            try:
                # 1. Fetch Positions from Kotak
                # Response format usually contains 'data': [{'realizedPNL': '100.00', 'netQty': '50', ...}]
                response = await kotak_adapter.get_positions()

                if not response or "data" not in response:
                    return

                total_realized_pnl = 0.0
                open_positions_count = 0

                # Check if data is a list (Kotak standard)
                positions = response.get("data", [])
                if positions is None:
                    positions = []

                for pos in positions:
                    # Sum Realized PnL
                    pnl = float(pos.get("realizedPNL", 0.0))
                    total_realized_pnl += pnl

                    # Count Open Positions (Net Qty != 0)
                    net_qty = int(pos.get("netQty", 0))
                    if net_qty != 0:
                        open_positions_count += 1

                # 2. Update Internal State
                self.current_pnl = total_realized_pnl
                self.open_trades = open_positions_count
                self.last_sync_time = datetime.now()

                logger.info(f"🛡️ State Synced: Realized PnL: ₹{self.current_pnl:.2f} | Open Trades: {self.open_trades}")

                # 3. Check for Breach immediately after sync
                if self.current_pnl <= -(self.config.max_daily_loss):
                    logger.critical(f"💀 STARTUP CHECK: Daily Loss Breached (₹{self.current_pnl}). Kill Switch ON.")
                    self.config.kill_switch_active = True

            except Exception as e:
                logger.error(f"❌ Risk Sync Failed: {e}")

    async def check_pre_trade(self, symbol: str, quantity: int, value: float) -> bool:
        async with self._lock:
            if self.config.kill_switch_active:
                logger.warning("⛔ KILL SWITCH ACTIVE. Trade Rejected.")
                return False

            # Check limits
            if self.current_pnl <= -(self.config.max_daily_loss):
                return False

            if self.open_trades >= self.config.max_concurrent_trades:
                return False

            # Reserve Slot
            self.open_trades += 1
            self.trades_today += 1
            return True

    async def on_execution_failure(self):
        """Rollback if order failed"""
        async with self._lock:
            self.open_trades = max(0, self.open_trades - 1)
            self.trades_today = max(0, self.trades_today - 1)

    async def update_post_trade_close(self, pnl: float):
        """
        Called by Strategy when it calculates a realized PnL.
        NOTE: We rely on frequent 'sync_state' calls to keep this accurate long-term,
        but this gives us immediate feedback.
        """
        async with self._lock:
            self.current_pnl += pnl
            self.open_trades = max(0, self.open_trades - 1)

            if self.current_pnl <= -(self.config.max_daily_loss):
                logger.critical("💀 DAILY LOSS LIMIT BREACHED. ACTIVATING KILL SWITCH.")
                self.config.kill_switch_active = True
