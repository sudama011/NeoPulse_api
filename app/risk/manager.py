import logging

from app.risk.models import PositionConfig, RiskConfig
from app.risk.sentinel import RiskSentinel
from app.risk.sizer import PositionSizer

logger = logging.getLogger("RiskManager")


class RiskManager:
    """
    Central Access Point for Risk Module.
    """

    def __init__(self, risk_config: RiskConfig, pos_config: PositionConfig):
        # Default Configs (Should be loaded from DB/Env in production)
        self.risk_config = risk_config
        self.pos_config = pos_config

        self.sentinel = RiskSentinel(self.risk_config)
        self.sizer = PositionSizer(self.pos_config)

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
