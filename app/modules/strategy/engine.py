import asyncio
import logging
from datetime import datetime, time
import pytz

from app.core.events import event_bus
from app.core.settings import settings
from app.adapters.kotak.client import kotak_client
from app.db.session import AsyncSessionLocal
from app.models.orders import OrderLedger
from sqlalchemy import select, func
from app.adapters.telegram.client import telegram_client
from app.modules.oms.execution import order_executor

logger = logging.getLogger("StrategyEngine")
IND = pytz.timezone("Asia/Kolkata")


class StrategyManager:
    """
    The Central Dispatcher.
    Routes incoming Ticks & Order Updates -> Correct Strategy Instance.
    Manages Auto-Square Off and Crash Recovery.
    """

    _instance = None

    def __init__(self):
        self.strategies = {}  # Map: token_id -> StrategyObject
        self.is_running = False
        self.available_capital = 0.0  # Tracked globally
        self.exit_time = time(15, 15)  # ⏰ 3:15 PM Auto Square Off

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = StrategyManager()
        return cls._instance

    def add_strategy(self, strategy_class, symbol: str, token: str):
        """
        Registers a new strategy for a specific stock.
        """
        token = str(token)

        # Avoid duplicate strategies for same token
        if token in self.strategies:
            logger.warning(f"⚠️ Strategy already exists for {symbol} ({token})")
            return

        # Instantiate the strategy
        strategy = strategy_class(symbol=symbol, token=token)
        self.strategies[token] = strategy
        logger.info(f"✅ Strategy Registered: {strategy.name} for {symbol}")

    async def square_off_all(self):
        """
        🚨 PANIC BUTTON / AUTO-STOP: Closes all open positions immediately.
        """
        logger.warning("🟥 INITIATING AUTO SQUARE OFF...")
        await telegram_client.send_alert(
            "🟥 <b>AUTO SQUARE OFF TRIGGERED (3:15 PM)</b>\nClosing all positions..."
        )

        tasks = []
        for token, strategy in self.strategies.items():
            if strategy.position != 0:
                # Determine Side to Exit
                side = "SELL" if strategy.position > 0 else "BUY"
                qty = abs(strategy.position)

                logger.info(f"📉 Squaring off {strategy.symbol}: {side} {qty}")

                # Place Market Order
                tasks.append(
                    order_executor.place_order(
                        symbol=strategy.symbol,
                        token=token,
                        side=side,
                        qty=qty,
                        price=0.0,  # Market Order
                    )
                )
                # Reset Internal State
                strategy.position = 0

        if tasks:
            await asyncio.gather(*tasks)
            await telegram_client.send_alert("✅ All positions closed.")
        else:
            await telegram_client.send_alert("ℹ️ No open positions to close.")

    async def reconcile(self):
        """
        RESTORE STATE: Aligns internal bot state with Reality (Broker/DB).
        """
        logger.info("🔄 Reconciling State (Crash Recovery)...")

        # 1. RESTORE CAPITAL (Baseline, can be overridden by API)
        if not settings.PAPER_TRADING:
            try:
                limits = kotak_client.get_limits()
                if limits and isinstance(limits, dict):
                    # Adapting to typical Kotak response; specific key might vary ('net', 'cash')
                    self.available_capital = float(
                        limits.get("net", 0.0) or limits.get("cash", 0.0)
                    )
                logger.info(f"💰 Live Capital Restored: ₹{self.available_capital}")
            except Exception as e:
                logger.error(f"❌ Failed to fetch limits: {e}")
        else:
            self.available_capital = settings.MAX_CAPITAL_ALLOCATION
            logger.info(f"💰 Paper Capital Default: ₹{self.available_capital}")

        # 2. RESTORE POSITIONS
        if not settings.PAPER_TRADING:
            # --- LIVE MODE: Trust the Broker ---
            try:
                positions = kotak_client.get_positions()
                if positions and "data" in positions:
                    for pos in positions["data"]:
                        token = str(pos.get("instrumentToken"))
                        qty = int(pos.get("quantity", 0))

                        if token in self.strategies:
                            self.strategies[token].position = qty
                            logger.warning(
                                f"⚠️ Restored LIVE Position: {self.strategies[token].symbol} = {qty}"
                            )
            except Exception as e:
                logger.error(f"❌ Failed to fetch positions: {e}")
        else:
            # --- PAPER MODE: Trust the Database ---
            try:
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(
                            OrderLedger.token,
                            func.sum(OrderLedger.quantity)
                            .filter(OrderLedger.transaction_type == "BUY")
                            .label("buys"),
                            func.sum(OrderLedger.quantity)
                            .filter(OrderLedger.transaction_type == "SELL")
                            .label("sells"),
                        ).group_by(OrderLedger.token)
                    )

                    rows = result.fetchall()
                    for row in rows:
                        token = str(row.token)
                        buys = row.buys or 0
                        sells = row.sells or 0
                        net_qty = buys - sells

                        if net_qty != 0 and token in self.strategies:
                            self.strategies[token].position = net_qty
                            logger.warning(
                                f"⚠️ Restored PAPER Position: {token} = {net_qty}"
                            )
            except Exception as e:
                logger.error(f"❌ DB Recovery Failed: {e}")

    async def _process_ticks(self):
        """
        Loop 1: Consumes Market Ticks & Checks Time.
        """
        logger.info("👂 Tick Listener Started.")
        while self.is_running:
            # ⏰ 1. CHECK TIME (Auto-Stop)
            now = datetime.now(IND).time()
            if now >= self.exit_time:
                logger.info("⏰ Market Closing Time Reached (3:15 PM). Stopping.")
                await self.square_off_all()
                self.is_running = False
                break

            try:
                # 2. Get Tick (Wait with timeout to allow loop to check time periodically)
                tick_payload = await asyncio.wait_for(
                    event_bus.tick_queue.get(), timeout=1.0
                )

                # Normalize Data (List vs Dict)
                ticks = []
                if isinstance(tick_payload, dict) and "data" in tick_payload:
                    ticks = tick_payload["data"]
                elif isinstance(tick_payload, list):
                    ticks = tick_payload

                # 3. Route to Strategy
                for tick in ticks:
                    token = str(tick.get("tk") or tick.get("instrumentToken"))

                    if token in self.strategies:
                        # Fire async task for strategy logic
                        asyncio.create_task(self.strategies[token].on_tick(tick))

            except asyncio.TimeoutError:
                continue  # Just checking the time...
            except Exception as e:
                logger.error(f"🔥 Tick Error: {e}")

    async def _process_orders(self):
        """
        Loop 2: Consumes Order Updates (Filled/Rejected).
        """
        logger.info("👂 Order Listener Started.")
        while self.is_running:
            try:
                order_payload = await event_bus.order_queue.get()

                # Kotak updates usually contain 'instrumentToken'
                token = str(order_payload.get("instrumentToken", ""))

                if token in self.strategies:
                    # Notify strategy of the update (e.g., mark position as filled)
                    asyncio.create_task(
                        self.strategies[token].on_order_update(order_payload)
                    )
            except Exception as e:
                logger.error(f"🔥 Order Update Error: {e}")

    async def start(self):
        """
        The Main Entry Point.
        """
        # 1. Recover State
        await self.reconcile()

        logger.info(f"🚀 Strategy Engine Starting. Capital: ₹{self.available_capital}")
        self.is_running = True

        # 2. Run Loops Concurrently
        # We run both Ticks (Price) and Orders (Status) in parallel
        await asyncio.gather(self._process_ticks(), self._process_orders())

        logger.info("🛑 Strategy Engine Stopped.")


# Global Singleton
strategy_engine = StrategyManager.get_instance()
