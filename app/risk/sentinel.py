import asyncio
import logging
from datetime import datetime

from app.execution.kotak import kotak_adapter
from app.schemas.common import RiskConfig

logger = logging.getLogger("RiskSentinel")


class RiskSentinel:
    """
    Enforces risk limits by synchronizing with Broker State.
    Calculates NET PnL (Gross - Approx Taxes) for safer Kill Switching.
    """

    def __init__(self, config: RiskConfig):
        self.config = config

        # State
        self.gross_pnl = 0.0
        self.net_pnl = 0.0  # The real number that matters
        self.estimated_charges = 0.0

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
        CRITICAL: Reconstructs state from Broker and calculates Net PnL.
        """
        # We acquire lock to prevent strategies from checking risk while we are updating
        async with self._lock:
            if not kotak_adapter.is_logged_in:
                # If we can't see the broker, we assume state is unchanged (or stale)
                # But we don't crash.
                return

            try:
                # 1. Fetch Positions
                response = await kotak_adapter.get_positions()
                if not response or "data" not in response:
                    return

                positions = response.get("data", [])
                if positions is None:
                    positions = []

                total_realized_gross = 0.0
                total_turnover = 0.0
                open_positions_count = 0

                for pos in positions:
                    # Gross PnL
                    try:
                        pnl = float(pos.get("realizedPNL", 0.0))
                        total_realized_gross += pnl
                    except ValueError:
                        pass

                    # Turnover Calculation (Buy Val + Sell Val)
                    try:
                        buy_amt = float(pos.get("buyAmt", 0.0))
                        sell_amt = float(pos.get("sellAmt", 0.0))
                        total_turnover += buy_amt + sell_amt
                    except ValueError:
                        pass

                    # Open Count
                    try:
                        net_qty = int(pos.get("netQty", 0))
                        if net_qty != 0:
                            open_positions_count += 1
                    except ValueError:
                        pass

                # 2. Update Internal State
                self.gross_pnl = total_realized_gross
                self.estimated_charges = total_turnover * 0.00035
                self.net_pnl = self.gross_pnl - self.estimated_charges

                self.open_trades = open_positions_count
                self.last_sync_time = datetime.now()

                logger.info(
                    f"🛡️ Sync: Gross: {self.gross_pnl:.0f} | "
                    f"Tax: {self.estimated_charges:.0f} | "
                    f"Net: {self.net_pnl:.0f} | "
                    f"Open: {self.open_trades}"
                )

                # 3. Kill Switch Check (Using NET PnL)
                if self.net_pnl <= -(self.config.max_daily_loss):
                    logger.critical(f"💀 DAILY LOSS LIMIT BREACHED (Net: {self.net_pnl:.2f}). KILL SWITCH ON.")
                    self.config.kill_switch_active = True

            except Exception as e:
                logger.error(f"❌ Risk Sync Failed: {e}")

    async def check_pre_trade(self, symbol: str, quantity: int, value: float) -> bool:
        """
        The Gatekeeper. Returns True if trade is allowed.
        """
        async with self._lock:
            # 1. Kill Switch
            if self.config.kill_switch_active:
                logger.warning("⛔ KILL SWITCH ACTIVE. Trade Rejected.")
                return False

            # 2. Drawdown Limit (Net PnL)
            if self.net_pnl <= -(self.config.max_daily_loss):
                logger.warning(f"⛔ Max Daily Loss Reached (Net: {self.net_pnl}). Trade Rejected.")
                return False

            # 3. Concurrency Limit
            if self.open_trades >= self.config.max_concurrent_trades:
                logger.warning(f"⛔ Max Concurrent Trades Reached ({self.open_trades}). Trade Rejected.")
                return False

            # 4. Optimistic Reservation
            # We increment here. If the trade fails later (in Execution),
            # 'on_execution_failure' MUST be called to decrement this.
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
            self.net_pnl += pnl
            self.gross_pnl += pnl
            self.open_trades = max(0, self.open_trades - 1)

            if self.net_pnl <= -(self.config.max_daily_loss):
                logger.critical("💀 DAILY LOSS LIMIT BREACHED. ACTIVATING KILL SWITCH.")
                self.config.kill_switch_active = True
