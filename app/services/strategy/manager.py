import asyncio
import logging
from datetime import datetime, time
import pytz
from sqlalchemy import select
from app.core.bus import event_bus
from app.core.settings import settings
from app.adapters.neo_client import neo_client
from app.db.session import AsyncSessionLocal
from app.models.orders import OrderLedger
from app.models.market_data import InstrumentMaster
from app.adapters.telegram_client import telegram_client
from app.services.oms.executor import order_executor
from app.services.strategy.lib.momentum import MomentumStrategy
from app.core.executors import run_blocking # <--- NEW IMPORT

logger = logging.getLogger("StrategyEngine")
IND = pytz.timezone("Asia/Kolkata")

class StrategyManager:
    _instance = None

    def __init__(self):
        self.strategies = {} 
        self.is_running = False
        self.available_capital = 0.0  
        self.exit_time = time(15, 15)  

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = StrategyManager()
        return cls._instance

    STRATEGY_MAP = {
        "MOMENTUM_TREND": MomentumStrategy,
    }

    async def configure(self, strategy_name: str, symbols: list, params: dict):
        # (Same as previous response: Fetch tokens from DB and register strategies)
        if strategy_name not in self.STRATEGY_MAP:
            raise ValueError(f"Unknown Strategy: {strategy_name}")

        StrategyClass = self.STRATEGY_MAP[strategy_name]
        self.strategies.clear()
        
        symbol_map = {}
        async with AsyncSessionLocal() as session:
            stmt = select(InstrumentMaster.trading_symbol, InstrumentMaster.instrument_token)\
                   .where(InstrumentMaster.trading_symbol.in_(symbols))
            result = await session.execute(stmt)
            for row in result.fetchall():
                symbol_map[row.trading_symbol] = str(row.instrument_token)

        for symbol in symbols:
            token = symbol_map.get(symbol)
            if token:
                self.add_strategy(StrategyClass, symbol, token)
                self.strategies[token].params = params

        logger.info(f"✅ Configured {len(self.strategies)}/{len(symbols)} tickers.")
    
    def add_strategy(self, strategy_class, symbol: str, token: str):
        token = str(token)
        if token not in self.strategies:
            strategy = strategy_class(symbol=symbol, token=token)
            self.strategies[token] = strategy
            logger.info(f"✅ Registered: {strategy.name} for {symbol}")

    async def square_off_all(self):
        # (Same as previous response)
        logger.warning("🟥 AUTO SQUARE OFF")
        await telegram_client.send_alert("🟥 <b>AUTO SQUARE OFF (3:15 PM)</b>")
        tasks = [
            order_executor.place_order(s.symbol, t, "SELL" if s.position > 0 else "BUY", abs(s.position), 0.0)
            for t, s in self.strategies.items() if s.position != 0
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def reconcile(self):
        """
        RESTORE STATE: Aligns internal bot state with Reality.
        Uses run_blocking for API calls.
        """
        logger.info("🔄 Reconciling State...")

        # 1. RESTORE CAPITAL
        if not settings.PAPER_TRADING:
            try:
                # ✅ REFACTORED: Offload Get Limits
                limits = await run_blocking(neo_client.get_limits)
                if limits and isinstance(limits, dict):
                    self.available_capital = float(limits.get("net", 0.0) or limits.get("cash", 0.0))
                logger.info(f"💰 Live Capital: ₹{self.available_capital}")
            except Exception as e:
                logger.error(f"❌ Failed to fetch limits: {e}")
        else:
            self.available_capital = settings.MAX_CAPITAL_ALLOCATION # Need to add this to Settings if not there, or hardcode

        # 2. RESTORE POSITIONS
        if not settings.PAPER_TRADING:
            try:
                # ✅ REFACTORED: Offload Get Positions
                positions = await run_blocking(neo_client.get_positions)
                if positions and "data" in positions:
                    for pos in positions["data"]:
                        token = str(pos.get("instrumentToken"))
                        qty = int(pos.get("quantity", 0))
                        if token in self.strategies:
                            self.strategies[token].position = qty
                            logger.warning(f"⚠️ Restored LIVE Pos: {self.strategies[token].symbol} = {qty}")
            except Exception as e:
                logger.error(f"❌ Failed to fetch positions: {e}")
        else:
            # (Paper logic remains pure async/DB, no changes needed)
            pass

    async def _process_ticks(self):
        # (Same as previous response)
        while self.is_running:
            try:
                tick_payload = await asyncio.wait_for(event_bus.tick_queue.get(), timeout=1.0)
                ticks = tick_payload.get("data", []) if isinstance(tick_payload, dict) else tick_payload
                for tick in ticks:
                    token = str(tick.get("tk") or tick.get("instrumentToken"))
                    if token in self.strategies:
                        asyncio.create_task(self.strategies[token].on_tick(tick))
            except asyncio.TimeoutError:
                continue
            except Exception:
                pass

    async def _process_orders(self):
        # (Same as previous response)
        while self.is_running:
            try:
                order_payload = await event_bus.order_queue.get()
                token = str(order_payload.get("instrumentToken", ""))
                if token in self.strategies:
                    asyncio.create_task(self.strategies[token].on_order_update(order_payload))
            except Exception:
                pass
                
    async def _heartbeat_loop(self):
        # (Same as previous response)
        while self.is_running:
            now = datetime.now()
            if now.time() >= self.exit_time:
                await self.square_off_all()
                self.is_running = False
                break
            for strategy in self.strategies.values():
                asyncio.create_task(strategy.on_time_update(now))
            await asyncio.sleep(1)

    async def start(self):
        await self.reconcile()
        logger.info(f"🚀 Strategy Engine Starting.")
        self.is_running = True
        await asyncio.gather(self._process_ticks(), self._process_orders(), self._heartbeat_loop())

strategy_engine = StrategyManager.get_instance()