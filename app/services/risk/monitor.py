import logging
from app.core.settings import settings

logger = logging.getLogger("RiskRules")

class RiskMonitor:

    def __init__(self):
        self.max_daily_loss = 0.0
        self.max_concurrent_trades = 1
        
        # Daily State
        self.current_pnl = 0.0
        self.trades_taken = 0

    def can_trade(self) -> bool:
        if self.current_pnl <= -self.max_daily_loss:
            logger.warning(f"🛑 Max Daily Loss Hit: {self.current_pnl}")
            return False
            
        if self.trades_taken >= self.max_trades:
            logger.warning(f"🛑 Max Daily Trades Hit: {self.trades_taken}")
            return False
            
        return True

    def update_pnl(self, realized_pnl):
        self.current_pnl += realized_pnl
        self.trades_taken += 1

    def reset_daily_stats(self):
        self.trades_taken = 0
        self.current_loss = 0.0
        logger.info("♻️ Risk Monitor Stats Reset for New Day")

    def update_config(self, max_daily_loss: float, max_concurrent_trades: int, risk_params: dict = None):
        """
        Updates risk limits dynamically from API request.
        """
        self.max_daily_loss = max_daily_loss
        self.max_concurrent_trades = max_concurrent_trades
        # Handle extra params if needed
        if risk_params:
            pass 
        logger.info(f"🛡️ Risk Config Updated: Max Loss={self.max_daily_loss}, Max Trades={self.max_trades_per_day}")

risk_monitor = RiskMonitor()