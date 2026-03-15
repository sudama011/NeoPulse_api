import logging

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.execution.kotak import kotak_adapter
from app.models.strategy import TradingSession
from app.risk.sentinel import RiskSentinel
from app.risk.sizer import PositionSizer
from app.schemas.common import RiskConfig

logger = logging.getLogger("RiskManager")


class RiskManager:
    def __init__(self):
        # Default safe config until DB loads
        self.config = RiskConfig(max_daily_loss=1000.0, max_concurrent_trades=3, risk_per_trade_pct=0.01)

        self.sentinel = RiskSentinel(self.config)
        self.sizer = PositionSizer()
        self.is_initialized = False
        self.total_allocated_capital = 100000.0

    async def initialize(self):
        """
        Loads config from active TradingSession and syncs with Broker.
        """
        logger.info("🛡️ Initializing Risk Manager...")

        # 1. Load Config from active TradingSession
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(TradingSession).where(TradingSession.is_active == True))
            trading_session = result.scalars().first()

            if trading_session:
                self.config.max_daily_loss = float(trading_session.max_daily_loss)
                self.config.max_concurrent_trades = int(trading_session.max_concurrent_trades)
                self.total_allocated_capital = float(trading_session.capital)

                logger.info(
                    f"✅ Risk Config Loaded from Session {trading_session.id}: "
                    f"Cap={self.total_allocated_capital}, MaxLoss={self.config.max_daily_loss}"
                )
            else:
                logger.warning("⚠️ No active TradingSession found. Using default risk config.")

        # 2. Sync State with Broker
        await self.sentinel.sync_state()
        self.is_initialized = True

    async def calculate_size(
        self, symbol: str, entry: float, sl: float, confidence: float = 1.0, leverage: float = 1.0
    ) -> int:
        """
        Smart Sizing wrapper.

        Args:
            symbol: Trading symbol
            entry: Entry price
            sl: Stop loss price
            confidence: Confidence multiplier (0.5 to 2.0)
            leverage: Strategy-specific leverage
        """
        from app.data.master import master_data  # Lazy import

        # 1. Get Live Limits
        available_cash = self.total_allocated_capital
        try:
            if kotak_adapter.is_logged_in:
                limits = await kotak_adapter.get_limits()
                # Kotak 'net' usually implies available margin
                net_val = limits.get("net", 0.0)
                if net_val:
                    available_cash = float(net_val)
        except Exception:
            pass  # Fallback to allocated capital

        # 2. Get Slot Info
        # open_slots = Total Allowed - Currently Used
        open_slots = max(0, self.config.max_concurrent_trades - self.sentinel.open_trades)

        # 3. Get Instrument Data
        inst_data = master_data.get_data(symbol)
        lot_size = inst_data.get("lot_size", 1) if inst_data else 1

        # 4. Calculate
        return self.sizer.calculate_qty(
            total_capital=self.total_allocated_capital,
            available_capital=available_cash,
            max_slots=self.config.max_concurrent_trades,
            open_slots=open_slots,
            entry_price=entry,
            sl_price=sl,
            lot_size=lot_size,
            confidence=confidence,
            risk_per_trade_pct=self.config.risk_per_trade_pct,
            leverage=leverage,
        )

    async def can_trade(self, symbol: str, qty: int, price: float) -> bool:
        if not self.is_initialized:
            logger.warning("⚠️ Risk Manager not initialized! Blocking trade.")
            return False
        value = qty * price
        return await self.sentinel.check_pre_trade(symbol, qty, value)

    async def on_execution_failure(self):
        await self.sentinel.on_execution_failure()

    async def on_trade_close(self, pnl: float):
        await self.sentinel.update_post_trade_close(pnl)


# Global Instance
risk_manager = RiskManager()
