import logging
import asyncio
from app.adapters.kotak.client import kotak_client
from app.core.settings import settings
from app.adapters.telegram.client import telegram_client 
from app.core.limiter import api_limiter

logger = logging.getLogger("OMS")

class OrderExecutor:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = OrderExecutor()
        return cls._instance

    async def place_order(self, symbol: str, token: str, side: str, qty: int, price: float = 0.0):
        # 🛡️ 1. WAIT FOR PERMISSION (Prevents Ban)
        await api_limiter.acquire()

        try:
            # 1. Prepare Alert Message
            emoji = "🔵" if side == "BUY" else "🔴"
            mode = "[PAPER]" if settings.PAPER_TRADING else "[LIVE]"
            
            # 🛑 PAPER TRADING CHECK
            if settings.PAPER_TRADING:
                logger.info(f"📝 [PAPER] {side} {qty} {symbol} @ {price or 'MKT'}")
                
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
                    "message": "Paper Order Placed", 
                    "data": {"orderId": "PAPER-123"}
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

            logger.info(f"✅ Broker Response: {response}")
            return response

        except Exception as e:
            logger.error(f"❌ Execution Failed: {e}")
            # Optional: Send Failure Alert
            asyncio.create_task(telegram_client.send_alert(f"⚠️ <b>ORDER FAILED:</b> {e}"))
            return None

order_executor = OrderExecutor.get_instance()