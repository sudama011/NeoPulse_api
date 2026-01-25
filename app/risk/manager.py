import logging
from app.risk.models import RiskConfig, PositionConfig
from app.risk.sentinel import RiskSentinel
from app.risk.sizer import PositionSizer

logger = logging.getLogger("RiskManager")

class RiskManager:
    """
    Central Access Point for Risk Module.
    """
    _instance = None

    def __init__(self):
        # Default Configs (Should be loaded from DB/Env in production)
        self.risk_config = RiskConfig(
            max_daily_loss=2000.0,
            max_capital_per_trade=50000.0,
            max_open_trades=3
        )
        self.pos_config = PositionConfig(
            method="FIXED_RISK",
            risk_per_trade_pct=0.01 # 1% Risk
        )
        
        self.sentinel = RiskSentinel(self.risk_config)
        self.sizer = PositionSizer(self.pos_config)

    @classmethod
    def get_instance(cls):
        if not cls._instance:
            cls._instance = RiskManager()
        return cls._instance

    async def initialize(self):
        """Syncs state from DB on startup."""
        await self.sentinel.sync_state()

    def calculate_size(self, capital: float, entry: float, sl: float) -> int:
        return self.sizer.calculate_qty(capital, entry, sl)

    async def can_trade(self, symbol: str, qty: int, price: float) -> bool:
        """Proxy to Sentinel."""
        value = qty * price
        return await self.sentinel.check_pre_trade(symbol, qty, value)
        
    async def on_execution_failure(self):
        """Proxy to Sentinel Rollback."""
        await self.sentinel.rollback_slot()

    async def on_trade_close(self, pnl: float):
        """Proxy to Sentinel Update."""
        await self.sentinel.update_post_trade_close(pnl)

# Global Instance
risk_manager = RiskManager.get_instance()