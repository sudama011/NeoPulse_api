import asyncio
import logging
from datetime import datetime, time
from typing import Dict, List, Type, Optional
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
from app.services.risk.position_sizer import CapitalManager
from app.core.executors import run_blocking
from app.core.circuit_breaker import positions_circuit_breaker, limits_circuit_breaker

logger = logging.getLogger("StrategyEngine")
IND = pytz.timezone("Asia/Kolkata")


class StrategyManager:
    """
    Central Strategy Engine Manager.
    
    Responsibilities:
    - Register and manage multiple strategy instances
    - Process market ticks and route to appropriate strategies
    - Coordinate square-off at market close
    - Reconcile live broker positions on startup
    
    Thread Safety:
    - Uses asyncio.Lock for strategies dict mutations
    - All async methods are coroutine-safe
    """
    
    _instance: Optional['StrategyManager'] = None
    
    # Supported strategies registry
    STRATEGY_MAP: Dict[str, Type] = {
        "MOMENTUM_TREND": MomentumStrategy,
    }

    def __init__(self):
        self.strategies: Dict[str, any] = {}  # {token: StrategyInstance}
        self.is_running: bool = False
        self.available_capital: float = 0.0
        self.exit_time: time = time(15, 15)  # 3:15 PM IST market close
        
        # ✅ Capital Manager for position sizing
        self.capital_manager: Optional[CapitalManager] = None
        
        # ✅ CRITICAL: Lock for thread-safe dict operations
        self._strategies_lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> 'StrategyManager':
        """Singleton accessor."""
        if cls._instance is None:
            cls._instance = StrategyManager()
        return cls._instance

    async def configure(
        self, 
        strategy_name: str, 
        symbols: List[str], 
        params: Dict
    ) -> None:
        """
        Configure and register strategies for given symbols.
        
        Args:
            strategy_name: Key in STRATEGY_MAP (e.g., "MOMENTUM_TREND")
            symbols: List of trading symbols (e.g., ["RELIANCE", "INFY"])
            params: Strategy-specific parameters dict
            
        Raises:
            ValueError: If strategy_name not found in STRATEGY_MAP
        """
        if strategy_name not in self.STRATEGY_MAP:
            raise ValueError(f"Unknown Strategy: {strategy_name}")

        StrategyClass = self.STRATEGY_MAP[strategy_name]
        
        # Initialize capital manager for position sizing
        # Default: 1% risk per trade on available capital
        risk_per_trade = params.get('risk_per_trade_pct', 0.01)
        self.capital_manager = CapitalManager(
            total_capital=self.available_capital,
            risk_per_trade_pct=risk_per_trade
        )
        logger.info(f"✅ CapitalManager initialized: Capital=₹{self.available_capital:,.2f}, Risk={risk_per_trade*100:.1f}%")
        
        # Fetch token mapping from DB
        symbol_map: Dict[str, str] = {}
        try:
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(InstrumentMaster.trading_symbol, InstrumentMaster.instrument_token)
                    .where(InstrumentMaster.trading_symbol.in_(symbols))
                )
                result = await session.execute(stmt)
                for row in result.fetchall():
                    symbol_map[row[0]] = str(row[1])
        except Exception as e:
            logger.error(f"❌ Failed to fetch symbols from DB: {e}")
            return

        # Register strategies (thread-safe)
        async with self._strategies_lock:
            self.strategies.clear()
            
            for symbol in symbols:
                token = symbol_map.get(symbol)
                if token:
                    await self._add_strategy_unsafe(StrategyClass, symbol, token)
                    if token in self.strategies:
                        self.strategies[token].params = params
                else:
                    logger.warning(f"⚠️ Symbol {symbol} not found in DB")

        logger.info(f"✅ Configured {len(self.strategies)}/{len(symbols)} tickers for {strategy_name}")
    
    async def _add_strategy_unsafe(
        self, 
        strategy_class: Type, 
        symbol: str, 
        token: str
    ) -> None:
        """
        Internal helper - adds strategy WITHOUT locking (must be called with lock held).
        
        Args:
            strategy_class: Class implementing BaseStrategy
            symbol: Trading symbol
            token: Instrument token
        """
        token = str(token)
        if token not in self.strategies:
            try:
                # Pass capital_manager to strategies that support it
                strategy = strategy_class(
                    symbol=symbol, 
                    token=token, 
                    capital_manager=self.capital_manager
                )
                self.strategies[token] = strategy
                logger.info(f"✅ Registered: {strategy.name} for {symbol}")
            except TypeError:
                # Fallback for strategies that don't accept capital_manager
                try:
                    strategy = strategy_class(symbol=symbol, token=token)
                    self.strategies[token] = strategy
                    logger.info(f"✅ Registered: {strategy.name} for {symbol}")
                except Exception as e:
                    logger.error(f"❌ Failed to register {symbol}: {e}")
            except Exception as e:
                logger.error(f"❌ Failed to register {symbol}: {e}")

    async def square_off_all(self) -> None:
        """
        Market close procedure: Exit all positions (AUTO SQUARE OFF).
        
        Called automatically at 3:15 PM IST.
        Sends close orders for all open positions.
        """
        logger.warning("🟥 AUTO SQUARE OFF - Market Close")
        
        try:
            await telegram_client.send_alert("🟥 <b>AUTO SQUARE OFF (3:15 PM IST)</b>\nClosing all positions...")
            
            # Gather close orders
            tasks = []
            async with self._strategies_lock:
                for token, strategy in self.strategies.items():
                    if strategy.position != 0:
                        # Opposite side to position
                        side = "SELL" if strategy.position > 0 else "BUY"
                        qty = abs(strategy.position)
                        
                        tasks.append(
                            order_executor.place_order(
                                strategy.symbol, 
                                token, 
                                side, 
                                qty, 
                                price=0.0
                            )
                        )
            
            # Execute all close orders concurrently
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                successful = sum(1 for r in results if not isinstance(r, Exception))
                logger.info(f"✅ Closed {successful}/{len(tasks)} positions")
                
        except Exception as e:
            logger.error(f"❌ Square off failed: {e}")

    async def reconcile(self) -> None:
        """
        RESTORE STATE: Aligns internal bot state with reality.
        
        On startup, fetch live broker state (if LIVE mode):
        - Current capital/margin
        - Current positions
        - Update internal state accordingly
        
        Uses run_blocking for synchronous broker API calls.
        Uses circuit breakers to prevent hanging on unavailable broker.
        """
        logger.info("🔄 Reconciling State with Broker...")

        # 1. RESTORE CAPITAL
        if not settings.PAPER_TRADING:
            try:
                # ✅ run_blocking wrapper + circuit breaker for sync Neo API
                limits = await limits_circuit_breaker.call(
                    run_blocking,
                    neo_client.get_limits
                )
                if limits and isinstance(limits, dict):
                    self.available_capital = float(limits.get("net", 0.0) or limits.get("cash", 0.0))
                    logger.info(f"💰 Live Capital Available: ₹{self.available_capital:,.2f}")
            except Exception as e:
                logger.error(f"❌ Failed to fetch capital limits: {e}")
                self.available_capital = 100000.0  # Fallback
        else:
            # Paper mode: Use hardcoded capital
            self.available_capital = getattr(settings, 'MAX_CAPITAL_ALLOCATION', 100000.0)
            logger.info(f"📝 [PAPER] Capital: ₹{self.available_capital:,.2f}")

        # 2. RESTORE POSITIONS
        if not settings.PAPER_TRADING:
            try:
                # ✅ run_blocking wrapper + circuit breaker for sync Neo API
                positions_resp = await positions_circuit_breaker.call(
                    run_blocking,
                    neo_client.get_positions
                )
                
                if positions_resp and "data" in positions_resp:
                    async with self._strategies_lock:
                        for pos_data in positions_resp["data"]:
                            token = str(pos_data.get("instrumentToken", ""))
                            qty = int(pos_data.get("quantity", 0))
                            
                            if token in self.strategies:
                                self.strategies[token].position = qty
                                logger.warning(
                                    f"⚠️ Restored LIVE Position: {self.strategies[token].symbol} = {qty} shares"
                                )
            except Exception as e:
                logger.error(f"❌ Failed to fetch live positions: {e}")

    async def _process_ticks(self) -> None:
        """
        Continuous tick processing loop.
        
        1. Dequeue ticks from event bus
        2. Route to relevant strategies
        3. Fire-and-forget tick processing (doesn't block on strategy execution)
        """
        logger.info("🔄 Tick processor starting")
        
        while self.is_running:
            try:
                # Get next tick payload with timeout
                tick_payload = await asyncio.wait_for(
                    event_bus.tick_queue.get(), 
                    timeout=2.0
                )
                
                # Extract ticks from payload
                ticks = (
                    tick_payload.get("data", []) 
                    if isinstance(tick_payload, dict) 
                    else [tick_payload]
                )
                
                # Route ticks to strategies
                async with self._strategies_lock:
                    for tick in ticks:
                        token = str(tick.get("tk") or tick.get("instrumentToken"))
                        if token in self.strategies:
                            # Fire-and-forget: don't block on strategy processing
                            asyncio.create_task(
                                self.strategies[token].on_tick(tick)
                            )
                            
            except asyncio.TimeoutError:
                # No ticks in queue - acceptable
                continue
            except Exception as e:
                logger.error(f"❌ Tick processing error: {e}")

    async def _process_orders(self) -> None:
        """
        Continuous order update processing loop.
        
        Handles filled/rejected orders from broker and notifies strategies.
        """
        logger.info("🔄 Order processor starting")
        
        while self.is_running:
            try:
                order_payload = await event_bus.order_queue.get()
                token = str(order_payload.get("instrumentToken", ""))
                
                async with self._strategies_lock:
                    if token in self.strategies:
                        # Fire-and-forget order update
                        asyncio.create_task(
                            self.strategies[token].on_order_update(order_payload)
                        )
                        
            except Exception as e:
                logger.error(f"❌ Order processing error: {e}")

    async def _heartbeat_loop(self) -> None:
        """
        1-second heartbeat loop.
        
        1. Check market close time
        2. Auto square-off if time reached
        3. Send time updates to all strategies (for candle closing)
        """
        logger.info("🕰️ Heartbeat started")
        
        while self.is_running:
            try:
                now = datetime.now(tz=IND)
                
                # Check market close time (3:15 PM)
                if now.time() >= self.exit_time:
                    logger.critical("🔴 Market Close Time Reached!")
                    await self.square_off_all()
                    self.is_running = False
                    break
                
                # Send time updates to all strategies
                async with self._strategies_lock:
                    tasks = [
                        strategy.on_time_update(now)
                        for strategy in self.strategies.values()
                    ]
                    if tasks:
                        await asyncio.gather(*tasks)
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"❌ Heartbeat error: {e}")

    async def start(self) -> None:
        """
        Start the strategy engine.
        
        Flow:
        1. Reconcile state with broker
        2. Mark as running
        3. Concurrently run: tick processor, order processor, heartbeat
        """
        logger.info("🚀 Strategy Engine Initializing...")
        
        # Startup reconciliation
        await self.reconcile()
        
        # Mark as running
        self.is_running = True
        logger.info("🟢 Strategy Engine Started!")
        
        # Run all processors concurrently until one dies
        try:
            await asyncio.gather(
                self._process_ticks(),
                self._process_orders(),
                self._heartbeat_loop()
            )
        except Exception as e:
            logger.error(f"❌ Strategy engine crashed: {e}")
            self.is_running = False


# Global Singleton
strategy_engine = StrategyManager.get_instance()