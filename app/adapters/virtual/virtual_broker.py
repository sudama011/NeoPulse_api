import pandas as pd
from datetime import datetime
import uuid

class VirtualBroker:
    def __init__(self, real_neo_client):
        self.client = real_neo_client  # The actual Kotak Neo API instance
        self.orders = {}  # Internal Order Book: {order_id: order_dict}
        self.positions = {} # {token: {qty: 10, avg_price: 1500}}
        self.ledger = [] # List of completed trades for PnL
        
        # Paper Account Config
        self.balance = 1000000.0  # ₹10 Lakh virtual capital

    # --- Passthrough Methods (Read-Only) ---
    def login(self, *args, **kwargs):
        return self.client.login(*args, **kwargs)
        
    def subscribe(self, *args, **kwargs):
        return self.client.subscribe(*args, **kwargs)

    # --- Intercepted Methods (Write) ---
    def place_order(self, order_params):
        """
        Mimics Kotak Neo v2 place_order response.
        """
        # Generate a fake Order ID (format matches Kotak's numeric style)
        fake_ord_id = str(int(datetime.now().timestamp() * 1000))
        
        # Store order in internal memory
        self.orders[fake_ord_id] = {
            "nOrdNo": fake_ord_id,
            "token": order_params.get("instrument_token"), # Assumes mapping
            "price": float(order_params.get("price", 0)),
            "trigger_price": float(order_params.get("trigger_price", 0)),
            "qty": int(order_params.get("quantity")),
            "type": order_params.get("order_type"), # 'L' or 'MKT'
            "side": order_params.get("transaction_type"), # 'B' or 'S'
            "status": "OPEN", 
            "filled_qty": 0,
            "timestamp": datetime.now()
        }
        
        # Return strict Kotak Neo JSON structure
        return {
            "stat": "Ok",
            "nOrdNo": fake_ord_id,
            "stCode": 200
        }

    def cancel_order(self, order_id):
        if order_id in self.orders:
            self.orders[order_id]['status'] = "CANCELLED"
            return {"stat": "Ok", "stCode": 200, "result": "Order Cancelled"}
        return {"stat": "Not_Ok", "stCode": 404, "errMsg": "Order not found"}

    # --- The Simulation Engine (The "Heartbeat") ---
    def process_candle(self, token, ohlc_candle):
        """
        Called every minute by the Strategy Engine with the latest 1-min candle.
        Decides if orders should be filled.
        """
        open_orders = [o for o in self.orders.values() if o['status'] == "OPEN"]
        
        for order in open_orders:
            if str(order['token'])!= str(token):
                continue
                
            is_filled = False
            fill_price = 0.0
            
            # FILL LOGIC
            if order['type'] == 'MKT':
                # Market orders fill at Open of the candle (Simulating instant execution)
                is_filled = True
                fill_price = ohlc_candle['open']
                
            elif order['type'] == 'L':
                if order['side'] == 'B': # Limit Buy
                    # If Candle Low is below Limit Price, we got filled
                    if ohlc_candle['low'] <= order['price']:
                        is_filled = True
                        # Fill at Limit Price (Conservative) or Open (if Open < Limit)
                        fill_price = min(order['price'], ohlc_candle['open']) 
                elif order['side'] == 'S': # Limit Sell
                    # If Candle High is above Limit Price, we got filled
                    if ohlc_candle['high'] >= order['price']:
                        is_filled = True
                        fill_price = max(order['price'], ohlc_candle['open'])

            if is_filled:
                self._execute_fill(order, fill_price)

    def _execute_fill(self, order, price):
        order['status'] = "TRADED"
        order['avg_price'] = price
        # Update Virtual Positions logic here...
        print(f"🔴 VIRTUAL FILL: {order['side']} {order['qty']} @ {price}")