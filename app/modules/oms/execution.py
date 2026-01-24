import logging
from app.adapters.kotak.client import kotak_client

logger = logging.getLogger("OMS")

class OrderExecutor:
    async def place_order(self, symbol: str, token: str, side: str, qty: int, price: float = 0):
        """
        Wrapper to place an order via Kotak SDK.
        """
        try:
            # Convert Side to Kotak Format
            txn_type = "B" if side.upper() == "BUY" else "S"
            
            logger.info(f"📡 Sending Order: {side} {qty} {symbol} @ {price or 'MKT'}")
            
            # Use the SDK (Adjust parameters based on SDK docs)
            # This is a generic call structure for Neo API v2
            response = kotak_client.client.place_order(
                exchange_segment="nse_cm",
                product="MIS", # Intraday
                price=str(price) if price > 0 else "0",
                order_type="L" if price > 0 else "MKT",
                quantity=str(qty),
                validity="DAY",
                trading_symbol=symbol,
                transaction_type=txn_type,
                amo="NO",
                disclosed_quantity="0",
                market_protection_percentage="0"
            )
            
            logger.info(f"✅ Order Response: {response}")
            return response

        except Exception as e:
            logger.error(f"❌ Order Failed: {e}")
            return None