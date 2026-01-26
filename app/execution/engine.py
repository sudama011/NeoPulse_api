import asyncio
import logging
import math

from app.core.settings import settings
from app.data.master import master_data
from app.execution.kotak import kotak_adapter
from app.execution.virtual import virtual_broker
from app.risk.manager import risk_manager

logger = logging.getLogger("ExecutionEngine")


class ExecutionEngine:
    """
    The High-Level Execution Controller (OMS).

    Responsibilities:
    1. Routing: Paper vs Live
    2. Slicing: Iceberg orders for large quantities
    3. Risk: Pre-trade checks
    4. Resilience: Retries and Error Handling
    """

    def __init__(self):
        self.risk_manager = risk_manager
        self.broker = virtual_broker if settings.PAPER_TRADING else kotak_adapter
        self.master_data = master_data

    async def initialize(self):
        """Connects to the active broker."""
        mode = "📝 PAPER" if settings.PAPER_TRADING else "💸 LIVE"
        logger.info(f"🚀 Initializing Execution Engine [{mode}]")
        await self.broker.login()

    async def execute_order(
        self, symbol: str, token: str, side: str, quantity: int, price: float = 0.0, tag: str = "STRATEGY"
    ):
        """
        Unified entry point for placing orders.
        Handles Slicing (Iceberg) and Risk checks automatically.
        """
        # 1. RISK CHECK (Atomic)
        if not await self.risk_manager.can_trade(symbol, quantity, price):
            logger.warning(f"🛑 Order Blocked by Risk Sentinel: {symbol} {side}")
            return None

        # 2. FETCH INSTRUMENT DATA
        inst_data = self.master_data.get_data(symbol)
        freeze_qty = 1800
        if inst_data:
            freeze_qty = inst_data.get("freeze_qty", 1800)

        # 3. ICEBERG LOGIC
        if quantity > freeze_qty:
            return await self._execute_iceberg(symbol, token, side, quantity, price)

        # 4. STANDARD EXECUTION
        return await self._send_single_order(symbol, token, side, quantity, price)

    async def _send_single_order(self, symbol, token, side, qty, price):
        """Constructs payload and sends to broker."""
        params = {
            "exchange_segment": "nse_cm",
            "trading_symbol": symbol,
            "instrument_token": token,
            "transaction_type": "B" if side.upper() == "BUY" else "S",
            "quantity": qty,
            "price": price,
            "order_type": "L" if price > 0 else "MKT",
            "product": "MIS",
            "validity": "DAY",
        }

        try:
            response = await self.broker.place_order(params)

            if response and response.get("stat") == "Ok":
                logger.info(f"✅ Order Sent: {symbol} {side} {qty} | ID: {response.get('nOrdNo')}")
                return response
            else:
                logger.error(f"❌ Order Rejected: {response.get('errMsg')}")
                # Rollback risk slot on rejection
                await self.risk_manager.on_execution_failure()
                return None

        except Exception as e:
            logger.error(f"🔥 Execution Exception: {e}")
            await self.risk_manager.on_execution_failure()
            return None

    async def _execute_iceberg(self, symbol, token, side, total_qty, price, freeze_limit):
        """Splits large orders into chunks."""
        num_legs = math.ceil(total_qty / freeze_limit)
        logger.info(f"🧊 ICEBERG ACTIVATED: Slicing {total_qty} into {num_legs} legs")

        remaining = total_qty
        responses = []

        for i in range(num_legs):
            leg_qty = min(remaining, freeze_limit)
            logger.info(f"🧊 Processing Leg {i + 1} / {num_legs}: {leg_qty} qty")

            resp = await self._send_single_order(symbol, token, side, leg_qty, price)
            responses.append(resp)

            if not resp:
                logger.error("❌ Iceberg Leg Failed! Stopping chain.")
                break

            remaining -= leg_qty
            # Small delay to prevent rate limit spam
            await asyncio.sleep(0.2)

        return responses


# Global Accessor
execution_engine = ExecutionEngine()
