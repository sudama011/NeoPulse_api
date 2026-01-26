import logging
from datetime import datetime
from app.execution.core import BrokerAdapter

logger = logging.getLogger("VirtualBroker")

class VirtualBrokerAdapter(BrokerAdapter):
    """
    Simulates a broker 100% in memory.
    Useful for Forward Testing without risk.
    """
    def __init__(self):
        self.orders = {}
        self.positions = {}
        self.balance = 100000.0 # Virtual ₹10L

    async def login(self):
        logger.info("📝 Virtual Broker Session Active (Paper Mode)")

    async def place_order(self, params: dict) -> dict:
        """
        Simulates order placement instantly.
        In reality, you'd match this against live ticks, but for simple testing,
        we assume 'MKT' orders fill instantly at LTP (passed via params or looked up).
        """
        order_id = f"VIRT_{int(datetime.now().timestamp()*1000)}"
        
        logger.info(
            f"📝 [PAPER TRADE] {params['transaction_type']} "
            f"{params['quantity']} {params['trading_symbol']} "
            f"@ {params.get('price', 'MKT')}"
        )
        
        # Store internal state
        self.orders[order_id] = {
            "id": order_id,
            "params": params,
            "status": "COMPLETE", # Auto-fill for simplicity
            "fill_price": float(params.get('price', 0)) or 1000.0 # Mock price
        }
        
        return {"stat": "Ok", "nOrdNo": order_id, "errMsg": None}

    async def cancel_order(self, order_id: str) -> dict:
        if order_id in self.orders:
            self.orders[order_id]['status'] = "CANCELLED"
            return {"stat": "Ok", "result": "Cancelled"}
        return {"stat": "Not_Ok", "errMsg": "Order not found"}

    async def get_positions(self) -> dict:
        # Mock Response matching Kotak Schema
        return {"stat": "Ok", "data": []}

    async def get_limits(self) -> dict:
        return {"stat": "Ok", "net": self.balance}

virtual_broker = VirtualBrokerAdapter()