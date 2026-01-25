import logging

logger = logging.getLogger("RiskMonitor")

class RiskMonitor:

    def __init__(self):
        self.max_daily_loss = 1000.0
        self.max_concurrent_trades = 3
        
        # Daily State
        self.current_pnl = 0.0
        self.trades_taken = 0

    def request_trade_slot(self) -> bool:
        """
        Atomically checks limits and reserves a trade slot.
        Must be called BEFORE placing an order.
        """
        # 1. Check PnL (Stop if we lost too much)
        if self.current_pnl <= -(self.max_daily_loss):
            logger.warning(f"🛑 Max Daily Loss Hit: Current PnL {self.current_pnl} <= -{self.max_daily_loss}")
            return False
            
        # 2. Check Trade Count
        if self.trades_taken >= self.max_concurrent_trades:
            logger.warning(f"🛑 Max Daily Trades Hit: {self.trades_taken}/{self.max_concurrent_trades}")
            return False
            
        # 3. Increment (Reserve Slot)
        self.trades_taken += 1
        return True

    def rollback_trade_slot(self):
        """
        Reverses the slot reservation if the order failed execution.
        """
        if self.trades_taken > 0:
            self.trades_taken -= 1
            logger.info("⏪ Trade slot rolled back due to execution failure.")

    def update_pnl(self, realized_pnl):
        self.current_pnl += realized_pnl

    def reset_daily_stats(self):
        self.trades_taken = 0
        self.current_pnl = 0.0 
        logger.info("♻️ Risk Monitor Stats Reset for New Day")

    def update_config(self, max_daily_loss: float, max_concurrent_trades: int, risk_params: dict = None):
        """
        Updates risk limits dynamically from API request.
        """
        self.max_daily_loss = max_daily_loss
        self.max_concurrent_trades = max_concurrent_trades 
        
        logger.info(f"🛡️ Risk Config Updated: Max Loss={self.max_daily_loss}, Max Trades={self.max_concurrent_trades}")

risk_monitor = RiskMonitor()