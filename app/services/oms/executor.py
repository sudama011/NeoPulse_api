import logging
import asyncio
from app.adapters.kotak.client import kotak_client
from app.core.settings import settings
from app.adapters.telegram.client import telegram_client 
from app.core.limiter import api_limiter
from app.services.risk.monitor import risk_monitor
from app.db.session import AsyncSessionLocal
from app.models.orders import OrderLedger
from datetime import datetime


logger = logging.getLogger("OMS")

class OrderExecutor:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = OrderExecutor()
        return cls._instance

    async def place_order(self, symbol: str, token: str, side: str, qty: int, price: float = 0.0):

        # We check if we are allowed to trade BEFORE asking the API Limiter
        if not risk_monitor.can_trade():
            logger.error(f"🛑 RISK BLOCK: Order rejected for {symbol}")
            await telegram_client.send_alert(f"🛑 <b>RISK BLOCK</b>\nTrade rejected for {symbol}. Daily limit reached.")
            return {"status": "error", "message": "Risk Limit Breached"}
        
        # 🛡️ 1. RATE LIMITER (Prevents Ban)
        await api_limiter.acquire()

        try:
            # 1. Prepare Alert Message
            emoji = "🔵" if side == "BUY" else "🔴"
            mode = "[PAPER]" if settings.PAPER_TRADING else "[LIVE]"
            
            # 🛑 PAPER TRADING CHECK
            if settings.PAPER_TRADING:
                logger.info(f"📝 [PAPER] {side} {qty} {symbol} @ {price or 'MKT'}")

                fake_order_id = f"PAPER-{int(datetime.now().timestamp())}"
                
                # 💾 SAVE TO DB (Crucial for Recovery)
                async with AsyncSessionLocal() as session:
                    db_order = OrderLedger(
                        order_id=fake_order_id,
                        token=token,
                        symbol=symbol,
                        transaction_type=side,
                        quantity=qty,
                        price=price if price > 0 else 0.0, # Approximate entry price
                        status="COMPLETE",
                        timestamp=datetime.now()
                    )
                    session.add(db_order)
                    await session.commit()
                
                # Send Alert
                msg = (
                    f"<b>{mode} ORDER PLACED</b>\n"
                    f"{emoji} <b>{side}</b> {symbol}\n"
                    f"🔢 Qty: {qty}\n"
                    f"💵 Price: {price or 'MKT'}\n"
                    f"⚡ Strategy: Momentum"
                )
                asyncio.create_task(telegram_client.send_alert(msg))
                
                return {
                    "status": "success", 
                    "orderId": fake_order_id
                }

            # 🚀 REAL TRADING
            logger.warning(f"💸 [LIVE] SENDING {side} {qty} {symbol}...")
            
            txn_type = "B" if side.upper() == "BUY" else "S"
            
            response = kotak_client.client.place_order(
                exchange_segment="nse_cm",
                product="MIS",
                price=str(price) if price > 0 else "0",
                order_type="L" if price > 0 else "MKT",
                quantity=str(qty),
                validity="DAY",
                trading_symbol=symbol,
                transaction_type=txn_type
            )
            
            # Send Alert for Real Trade too
            if response and response.get("stat") == "Ok":
                msg = (
                    f"<b>{mode} ORDER SENT</b>\n"
                    f"{emoji} <b>{side}</b> {symbol}\n"
                    f"🔢 Qty: {qty}\n"
                    f"🆔 OrderID: {response.get('nOrdNo', 'Unknown')}"
                )
                asyncio.create_task(telegram_client.send_alert(msg))
            
            risk_monitor.trades_taken += 1

            logger.info(f"✅ Broker Response: {response}")
            return response

        except Exception as e:
            logger.error(f"❌ Execution Failed: {e}")
            # Optional: Send Failure Alert
            asyncio.create_task(telegram_client.send_alert(f"⚠️ <b>ORDER FAILED:</b> {e}"))
            return None

order_executor = OrderExecutor.get_instance()