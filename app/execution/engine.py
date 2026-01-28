import asyncio
import logging
import math
import uuid
from datetime import datetime
from typing import Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.data.master import master_data
from app.db.session import AsyncSessionLocal
from app.execution.kotak import kotak_adapter
from app.execution.virtual import virtual_broker
from app.models.orders import OrderLedger
from app.risk.manager import risk_manager
from app.schemas.execution import OrderResponse, OrderStatus

logger = logging.getLogger("ExecutionEngine")


class ExecutionEngine:
    def __init__(self):
        self.risk_manager = risk_manager
        self.broker = virtual_broker if settings.PAPER_TRADING else kotak_adapter
        self.master_data = master_data

    async def initialize(self):
        mode = "📝 PAPER" if settings.PAPER_TRADING else "💸 LIVE"
        logger.info(f"🚀 Initializing Execution Engine [{mode}]")
        await self.broker.login()

    async def execute_order(
        self, symbol: str, token: str, side: str, quantity: int, price: float = 0.0, tag: str = "STRATEGY"
    ) -> Union[OrderResponse, None]:
        """
        Unified entry point. Persists order to DB and Routes to Broker.
        """
        # 1. RISK CHECK
        if not await self.risk_manager.can_trade(symbol, quantity, price):
            logger.warning(f"🛑 Order Blocked by Risk Sentinel: {symbol} {side}")
            return None

        # 2. FETCH DATA
        inst_data = self.master_data.get_data(symbol)
        freeze_qty = inst_data.get("freeze_qty", 1800) if inst_data else 1800

        # 3. ROUTE (Standard vs Iceberg)
        if quantity > freeze_qty:
            return await self._execute_iceberg(symbol, token, side, quantity, price, freeze_qty, tag)
        else:
            return await self._send_single_order(symbol, token, side, quantity, price, tag)

    async def _send_single_order(self, symbol, token, side, qty, price, tag) -> OrderResponse:
        """
        Executes a single atomic order with DB persistence.
        """
        internal_id = uuid.uuid4()

        # A. PRE-ORDER PERSISTENCE (Audit Trail)
        async with AsyncSessionLocal() as session:
            try:
                ledger_entry = OrderLedger(
                    internal_id=internal_id,
                    token=int(token),
                    transaction_type=side,
                    quantity=qty,
                    price=price,
                    order_type="L" if price > 0 else "MKT",
                    product="MIS",
                    status="PENDING_BROKER",
                    strategy_id=tag,
                )
                session.add(ledger_entry)
                await session.commit()
            except Exception as e:
                logger.error(f"❌ DB Error (Pre-Order): {e}")
                # We do NOT abort trade on DB error, but we log critically

        # B. BROKER EXECUTION
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
            "tag": tag,  # Pass tag to broker if supported
        }

        response_obj = None

        try:
            # Broker returns raw dict
            raw_resp = await self.broker.place_order(params)

            # --- STANDARDIZE RESPONSE ---
            is_success = raw_resp.get("stat") == "Ok" or "nOrdNo" in raw_resp

            if is_success:
                order_id = raw_resp.get("nOrdNo", "UNKNOWN")
                logger.info(f"✅ Order Sent: {symbol} {side} {qty} | ID: {order_id}")

                response_obj = OrderResponse(
                    order_id=order_id,
                    status=OrderStatus.COMPLETE,
                    filled_qty=qty,
                    average_price=price,
                    raw_response=raw_resp,
                )
            else:
                msg = raw_resp.get("errMsg", "Unknown Broker Error")
                logger.error(f"❌ Order Rejected: {msg}")
                await self.risk_manager.on_execution_failure()

                response_obj = OrderResponse(
                    order_id="NA", status=OrderStatus.REJECTED, error_message=msg, raw_response=raw_resp
                )

        except Exception as e:
            logger.error(f"🔥 Execution Exception: {e}")
            await self.risk_manager.on_execution_failure()
            response_obj = OrderResponse(order_id="NA", status=OrderStatus.FAILED, error_message=str(e))

        # C. POST-ORDER UPDATE
        # We fire-and-forget this update so we don't block the strategy loop
        asyncio.create_task(self._update_ledger(internal_id, response_obj))

        return response_obj

    async def _update_ledger(self, internal_id: uuid.UUID, response: OrderResponse):
        """Updates the DB asynchronously."""
        async with AsyncSessionLocal() as session:
            try:
                stmt = select(OrderLedger).where(OrderLedger.internal_id == internal_id)
                result = await session.execute(stmt)
                entry = result.scalars().first()

                if entry:
                    entry.exchange_id = response.order_id if response.order_id != "NA" else None
                    entry.status = response.status.value
                    entry.raw_response = response.raw_response
                    if response.error_message:
                        entry.rejection_reason = response.error_message

                    await session.commit()
            except Exception as e:
                logger.error(f"❌ DB Error (Post-Order): {e}")

    async def _execute_iceberg(self, symbol, token, side, total_qty, price, freeze_limit, tag) -> OrderResponse:
        """
        Smart Iceberg: Aggregates results of multiple legs.
        """
        num_legs = math.ceil(total_qty / freeze_limit)
        logger.info(f"🧊 ICEBERG: {total_qty} qty -> {num_legs} legs (Limit: {freeze_limit})")

        remaining = total_qty
        filled_so_far = 0
        order_ids = []
        errors = []

        for i in range(num_legs):
            leg_qty = min(remaining, freeze_limit)

            # Execute Leg
            resp = await self._send_single_order(symbol, token, side, leg_qty, price, f"{tag}_ICE_{i+1}")

            if resp.status == OrderStatus.COMPLETE:
                filled_so_far += leg_qty
                remaining -= leg_qty
                order_ids.append(resp.order_id)
                await asyncio.sleep(0.2)  # Rate limit protection
            else:
                logger.error(f"❌ Iceberg Leg {i + 1} Failed! Stopping chain. Reason: {resp.error_message}")
                errors.append(f"Leg {i+1}: {resp.error_message}")
                break

        # Generate Consolidated Report
        final_status = OrderStatus.COMPLETE if filled_so_far == total_qty else OrderStatus.PARTIAL
        if filled_so_far == 0:
            final_status = OrderStatus.FAILED

        return OrderResponse(
            order_id=",".join(order_ids),
            status=final_status,
            filled_qty=filled_so_far,
            average_price=price,
            error_message=" | ".join(errors) if errors else None,
        )


execution_engine = ExecutionEngine()
