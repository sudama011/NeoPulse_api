import logging
import asyncio
from app.adapters.neo_client import neo_client
from app.core.settings import settings
from app.adapters.telegram_client import telegram_client 
from app.core.limiter import api_limiter
from app.services.risk.monitor import risk_monitor
from app.db.session import AsyncSessionLocal
from app.models.orders import OrderLedger
from datetime import datetime
from app.core.executors import run_blocking

logger = logging.getLogger("OMS")

class OrderExecutor:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = OrderExecutor()
        return cls._instance

    async def place_order(self, symbol: str, token: str, side: str, qty: int, price: float = 0.0):

        # 🛡️ 1. ATOMIC RISK CHECK
        if not await risk_monitor.request_trade_slot():
            logger.error(f"🛑 RISK BLOCK: Order rejected for {symbol}")
            await telegram_client.send_alert(f"🛑 <b>RISK BLOCK</b>\nTrade rejected for {symbol}. Daily limit reached.")
            return {"status": "error", "message": "Risk Limit Breached"}
        
        # 🛡️ 2. RATE LIMITER
        await api_limiter.acquire()

        try:
            emoji = "🔵" if side == "BUY" else "🔴"
            mode = "[PAPER]" if settings.PAPER_TRADING else "[LIVE]"
            
            # --- 📝 PAPER TRADING ---
            if settings.PAPER_TRADING:
                logger.info(f"📝 [PAPER] {side} {qty} {symbol} @ {price or 'MKT'}")
                fake_order_id = f"PAPER-{int(datetime.now().timestamp())}"
                
                async with AsyncSessionLocal() as session:
                    db_order = OrderLedger(
                        internal_id=fake_order_id,
                        token=int(token),
                        transaction_type=side,
                        quantity=qty,
                        price=price if price > 0 else 0.0,
                        status="COMPLETE"
                    )
                    session.add(db_order)
                    await session.commit()
                
                msg = (
                    f"<b>{mode} ORDER PLACED</b>\n"
                    f"{emoji} <b>{side}</b> {symbol}\n"
                    f"🔢 Qty: {qty}\n"
                    f"💵 Price: {price or 'MKT'}"
                )
                asyncio.create_task(telegram_client.send_alert(msg))
                return {"status": "success", "orderId": fake_order_id}

            # --- 🚀 LIVE TRADING ---
            logger.warning(f"💸 [LIVE] SENDING {side} {qty} {symbol}...")
            txn_type = "B" if side.upper() == "BUY" else "S"
            
            # ✅ REFACTORED: Use shared run_blocking helper
            response = await run_blocking(
                neo_client.client.place_order,
                exchange_segment="nse_cm",
                product="MIS",
                price=str(price) if price > 0 else "0",
                order_type="L" if price > 0 else "MKT",
                quantity=str(qty),
                validity="DAY",
                trading_symbol=symbol,
                transaction_type=txn_type
            )
            
            if response and response.get("stat") == "Ok":
                msg = (
                    f"<b>{mode} ORDER SENT</b>\n"
                    f"{emoji} <b>{side}</b> {symbol}\n"
                    f"🔢 Qty: {qty}\n"
                    f"🆔 OrderID: {response.get('nOrdNo', 'Unknown')}"
                )
                asyncio.create_task(telegram_client.send_alert(msg))
                logger.info(f"✅ Broker Response: {response}")
                return response
            else:
                raise Exception(f"Broker Rejected: {response}")

        except Exception as e:
            logger.error(f"❌ Execution Failed: {e}")
            await risk_monitor.rollback_trade_slot()
            asyncio.create_task(telegram_client.send_alert(f"⚠️ <b>ORDER FAILED:</b> {e}"))
            return None

order_executor = OrderExecutor.get_instance()