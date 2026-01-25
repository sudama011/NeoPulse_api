import logging
from app.core.settings import settings

logger = logging.getLogger("RiskRules")

class RiskMonitor:
    def __init__(self, max_daily_loss: float, max_trades_per_day: int):
        self.max_daily_loss = max_daily_loss
        self.max_trades = max_trades_per_day
        
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

risk_monitor = RiskMonitor(
    max_daily_loss=settings.MAX_DAILY_LOSS, 
    max_trades_per_day=settings.MAX_CONCURRENT_TRADES
)