import logging
from app.execution.core import BrokerAdapter

logger = logging.getLogger("BacktestSimulator")

class BacktestBroker(BrokerAdapter):
    """
    Simulates execution instantly.
    
    Unlike VirtualBroker (which is async/real-time), this broker 
    fills orders based on the CURRENT candle's High/Low.
    """
    
    def __init__(self, initial_capital: float = 100000.0):
        self.balance = initial_capital
        self.initial_capital = initial_capital
        self.positions = {}  # {token: qty}
        self.orders = []     # History of trades
        self.current_candle = {} # Updated by Engine every step

    async def login(self):
        pass # No login needed for simulation

    def update_candle(self, candle: dict):
        """Engine calls this to let Broker know current prices."""
        self.current_candle = candle

    async def place_order(self, params: dict) -> dict:
        symbol = params['trading_symbol']
        side = params['transaction_type'] # BUY/SELL
        qty = int(params['quantity'])
        
        # Price Simulation:
        # In backtest, we assume we get filled at CLOSE of the candle
        # (Conservative assumption). Realistically could be Open/VWAP.
        fill_price = self.current_candle.get('close', 0.0)
        
        cost = fill_price * qty
        
        # 1. Validation
        if side == "BUY":
            if cost > self.balance:
                logger.warning("❌ Insufficient Funds for Backtest Trade")
                return {"stat": "Not_Ok"}
            self.balance -= cost
            self.positions[symbol] = self.positions.get(symbol, 0) + qty
            
        elif side == "SELL":
            # For simplicity in backtest, we allow shorting (negative qty)
            # or closing existing.
            self.balance += cost
            self.positions[symbol] = self.positions.get(symbol, 0) - qty

        # 2. Record Trade
        trade_record = {
            "time": self.current_candle.get('start_time'),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": fill_price,
            "value": cost,
            "balance_after": self.balance
        }
        self.orders.append(trade_record)
        logger.debug(f"📝 Backtest Trade: {side} {qty} @ {fill_price:.2f}")

        return {"stat": "Ok", "nOrdNo": f"BT_{len(self.orders)}"}

    async def cancel_order(self, order_id: str):
        return {"stat": "Ok"}

    async def get_positions(self):
        return {"data": self.positions}

    async def get_limits(self):
        return {"net": self.balance}