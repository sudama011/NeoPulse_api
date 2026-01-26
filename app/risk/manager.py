import logging

from app.data.master import master_data
from app.risk.models import PositionConfig, RiskConfig
from app.risk.sentinel import RiskSentinel
from app.risk.sizer import PositionSizer

logger = logging.getLogger("RiskManager")


class RiskManager:
    """
    Central Access Point for Risk Module.
    Orchestrates Sentinel (Limits) and Sizer (Math).
    """

    def __init__(self, risk_config: RiskConfig, pos_config: PositionConfig):
        self.risk_config = risk_config
        self.pos_config = pos_config

        self.sentinel = RiskSentinel(self.risk_config)
        self.sizer = PositionSizer(self.pos_config)

    async def initialize(self):
        """Syncs state from DB on startup."""
        await self.sentinel.sync_state()

    def calculate_size(self, symbol: str, capital: float, entry: float, sl: float) -> int:
        """
        Calculates position size including Lot Size lookup.
        """
        # 1. Fetch Instrument Details
        inst_data = master_data.get_data(symbol)
        lot_size = inst_data.get("lot_size", 1) if inst_data else 1

        # 2. Calculate
        return self.sizer.calculate_qty(capital, entry, sl, lot_size=lot_size)

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


# Factory/Singleton instance setup
# (Configuration usually comes from settings/env in a real app)
risk_manager = RiskManager(
    risk_config=RiskConfig(max_daily_loss=2000.0, max_capital_per_trade=50000.0, max_open_trades=3),
    pos_config=PositionConfig(method="FIXED_RISK", risk_per_trade_pct=0.01),
)
