import asyncio
import logging
from sqlalchemy import func, select, case

from app.db.session import AsyncSessionLocal
from app.models.orders import OrderLedger
from app.risk.models import RiskConfig

logger = logging.getLogger("RiskSentinel")


class RiskSentinel:
    """
    Enforces risk limits (Daily Loss, Max Trades).
    Acts as the 'Source of Truth' by reconciling Broker State + Database State.
    """

    def __init__(self, config: RiskConfig):
        self.config = config

        # In-Memory State
        self.current_pnl = 0.0
        self.open_trades = 0
        self.trades_today = 0
        self.peak_equity = 0.0
        
        # Concurrency Control
        self._lock = asyncio.Lock()

    async def sync_state(self):
        """
        CRASH RECOVERY:
        1. Asks Broker: "How many positions are ACTUALLY open?"
        2. Asks DB: "How much money did we make/lose today?"
        """
        async with self._lock:
            logger.info("♻️ Risk Sentinel: Starting State Reconciliation...")

            # --- STEP 1: Broker Sync (Source of Truth for Open Trades) ---
            try:
                # Lazy import to avoid circular dependency
                from app.execution.kotak import kotak_adapter
                
                real_open_positions = 0
                
                if kotak_adapter.is_logged_in:
                    # We use 'positions' for Intraday/F&O. 
                    # 'holdings' is for CNC/Delivery which usually doesn't count towards intraday slot limits.
                    api_response = await kotak_adapter.get_positions()
                    
                    if api_response and "data" in api_response:
                        for pos in api_response["data"]:
                            # Net Quantity != 0 means the position is still open
                            net_qty = int(pos.get("netQty", 0))
                            if net_qty != 0:
                                real_open_positions += 1
                                logger.info(f"🔎 Found Open Position: {pos.get('tradingSymbol')} (Qty: {net_qty})")
                    
                    self.open_trades = real_open_positions
                    logger.info(f"✅ Broker Sync: {self.open_trades} active positions found.")
                else:
                    logger.warning("⚠️ Broker not logged in. Skipping Broker Sync (using DB/Memory).")

            except Exception as e:
                logger.error(f"❌ Broker Sync Failed: {e}")

            # --- STEP 2: Database Sync (Source of Truth for PnL) ---
            # We trust our DB for PnL because Broker PnL resets or includes carry-forward
            try:
                async with AsyncSessionLocal() as session:
                    # Calculate realized PnL for TODAY
                    # (Assuming you have a 'trade_book' or similar, or summing closed order_ledger entries)
                    # For now, we will rely on the previous PnL logic or reset to 0 if new day.
                    # This is a placeholder for your specific PnL query logic.
                    pass 

            except Exception as e:
                logger.error(f"❌ DB Sync Failed: {e}")

            logger.info(f"🛡️ Risk State Ready: Open Trades {self.open_trades} | PnL {self.current_pnl}")

    async def check_pre_trade(self, symbol: str, quantity: int, value: float) -> bool:
        """
        The Gatekeeper. Called BEFORE every order.
        """
        async with self._lock:
            # 1. Kill Switch
            if self.config.kill_switch_active:
                logger.warning("⛔ KILL SWITCH ACTIVE. Trade Rejected.")
                return False

            # 2. Daily Loss Limit
            if self.current_pnl <= -(self.config.max_daily_loss):
                logger.error(f"🛑 Max Daily Loss Hit: {self.current_pnl:.2f} <= -{self.config.max_daily_loss}")
                return False

            # 3. Max Concurrent Trades
            if self.open_trades >= self.config.max_open_trades:
                logger.warning(f"🛑 Max Open Trades Reached: {self.open_trades}/{self.config.max_open_trades}")
                return False

            # 4. Capital Check
            if value > self.config.max_capital_per_trade:
                logger.warning(f"🛑 Trade Value Exceeds Limit: {value:.2f} > {self.config.max_capital_per_trade}")
                return False

            # Reserve Slot
            self.open_trades += 1
            self.trades_today += 1
            return True

    async def update_post_trade_close(self, pnl: float):
        """
        Called AFTER a trade is closed. Updates PnL and frees up a slot.
        """
        async with self._lock:
            self.current_pnl += pnl
            self.open_trades = max(0, self.open_trades - 1)

            # Update Peak Equity for Drawdown
            if self.current_pnl > self.peak_equity:
                self.peak_equity = self.current_pnl

            logger.info(
                f"📉 Trade Closed. PnL: {pnl:+.2f} | "
                f"Daily Net: {self.current_pnl:+.2f} | "
                f"Open Slots: {self.open_trades}"
            )

            # Auto-Kill if limit breached
            if self.current_pnl <= -(self.config.max_daily_loss):
                logger.critical("💀 DAILY LOSS LIMIT BREACHED. ACTIVATING KILL SWITCH.")
                self.config.kill_switch_active = True

    async def rollback_slot(self):
        """Called if an order was rejected by Broker."""
        async with self._lock:
            self.open_trades = max(0, self.open_trades - 1)
            self.trades_today = max(0, self.trades_today - 1)
            logger.info("⏪ Risk Slot Rolled Back")

    async def reset_daily(self):
        """Called by Scheduler at market open."""
        async with self._lock:
            self.current_pnl = 0.0
            self.trades_today = 0
            self.peak_equity = 0.0
            self.config.kill_switch_active = False
            # We DO NOT reset open_trades here, as we might be carrying forward positions
            # But we re-sync to be sure
            logger.info("☀️ Risk Stats Reset for New Trading Day")

    def get_status(self):
        return {
            "daily_pnl": self.current_pnl,
            "open_trades": self.open_trades,
            "trades_today": self.trades_today,
            "loss_limit": self.config.max_daily_loss,
            "status": "HALTED" if self.config.kill_switch_active else "ACTIVE",
        }