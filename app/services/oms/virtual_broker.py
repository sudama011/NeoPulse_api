import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger("VirtualBroker")


class Position:
    """
    Represents a single position in virtual broker.
    Tracks average entry price, cumulative quantity, and PnL.
    """

    def __init__(self):
        self.qty = 0
        self.avg_entry_price = 0.0
        self.current_price = 0.0

    def add(self, qty: int, price: float) -> None:
        """Add to position (scale in)."""
        if qty == 0:
            return

        # Weighted average calculation
        total_qty = abs(self.qty) + abs(qty)
        if total_qty > 0:
            self.avg_entry_price = (abs(self.qty) * self.avg_entry_price + abs(qty) * price) / total_qty
        self.qty += qty
        self.current_price = price

    def close_partial(self, qty: int, price: float) -> float:
        """Close partial position. Returns realized PnL."""
        if qty == 0:
            return 0.0

        # Calculate realized PnL on closed quantity
        side = 1 if self.qty > 0 else -1
        realized_pnl = side * qty * (price - self.avg_entry_price)

        self.qty -= qty * side  # Reduce position
        self.current_price = price

        return realized_pnl

    def get_unrealized_pnl(self, current_price: float) -> float:
        """Calculate unrealized PnL at current market price."""
        if self.qty == 0:
            return 0.0

        side = 1 if self.qty > 0 else -1
        return side * abs(self.qty) * (current_price - self.avg_entry_price)


class VirtualBroker:
    """
    Paper Trading Broker Simulator.

    Mimics Kotak Neo API behavior:
    - Accepts/rejects orders based on market conditions
    - Simulates order fills on candle data
    - Tracks positions with realistic FIFO matching
    - Calculates realized and unrealized PnL

    Thread-Safety: Use asyncio.Lock if calling from multiple coroutines
    """

    def __init__(self, real_neo_client):
        """
        Initialize virtual broker backed by real Neo client for market data.

        Args:
            real_neo_client: The actual Kotak Neo API instance (for read-only methods)
        """
        self.client = real_neo_client  # The actual Kotak Neo API instance
        self.orders: Dict[str, Dict] = {}  # {order_id: order_dict}
        self.positions: Dict[str, Position] = {}  # {token: Position}
        self.ledger: List[Dict] = []  # Completed trades (limited to last 1000)
        self.ledger_max_size = 1000

        # Paper Account Config
        self.initial_balance = 1000000.0  # ₹10 Lakh virtual capital
        self.balance = self.initial_balance
        self.total_realized_pnl = 0.0

    # --- Read-Only Passthrough Methods ---
    def login(self, *args, **kwargs):
        """Passthrough to real client."""
        return self.client.login(*args, **kwargs)

    def subscribe(self, *args, **kwargs):
        """Passthrough to real client."""
        return self.client.subscribe(*args, **kwargs)

    def get_scrip_master(self, *args, **kwargs):
        """Passthrough to real client."""
        return self.client.get_scrip_master(*args, **kwargs)

    # --- Intercepted Methods (Write) ---
    def place_order(self, order_params: Dict) -> Dict:
        """
        Simulates order placement.

        Args:
            order_params: Order details matching Kotak Neo v2 format
                {
                    'exchange_segment': 'nse_cm',
                    'trading_symbol': 'RELIANCE',
                    'instrument_token': '12345',
                    'transaction_type': 'B' (Buy) or 'S' (Sell),
                    'quantity': 25,
                    'price': 1500.0 or 0,
                    'order_type': 'L' (Limit) or 'MKT' (Market),
                    'validity': 'DAY'
                }

        Returns:
            Kotak Neo response format:
            {'stat': 'Ok', 'nOrdNo': order_id, 'stCode': 200}
        """
        # Generate fake Order ID (matches Kotak's numeric format)
        fake_order_id = str(int(datetime.now().timestamp() * 1000))

        # Validate order parameters
        try:
            token = str(order_params.get("instrument_token", ""))
            qty = int(order_params.get("quantity", 0))
            price = float(order_params.get("price", 0))
            side = order_params.get("transaction_type", "")
            order_type = order_params.get("order_type", "MKT")

            if not all([token, qty > 0, side in ["B", "S"], order_type in ["L", "MKT"]]):
                return {"stat": "Not_Ok", "stCode": 400, "errMsg": "Invalid order parameters"}

        except (ValueError, KeyError) as e:
            logger.error(f"❌ Order validation failed: {e}")
            return {"stat": "Not_Ok", "stCode": 400, "errMsg": str(e)}

        # Store order in internal memory
        self.orders[fake_order_id] = {
            "nOrdNo": fake_order_id,
            "token": token,
            "price": price,
            "trigger_price": float(order_params.get("trigger_price", 0)),
            "qty": qty,
            "type": order_type,  # 'L' or 'MKT'
            "side": side,  # 'B' or 'S'
            "status": "OPEN",
            "filled_qty": 0,
            "avg_price": 0.0,
            "timestamp": datetime.now(),
        }

        logger.info(
            f"📝 [PAPER] Order Placed: {side} {qty} Token({token}) "
            f"@ {price if price > 0 else 'MKT'} | OrderID: {fake_order_id}"
        )

        # Return strict Kotak Neo JSON structure
        return {"stat": "Ok", "nOrdNo": fake_order_id, "stCode": 200}

    def cancel_order(self, order_id: str) -> Dict:
        """Cancel an open order."""
        if order_id not in self.orders:
            return {"stat": "Not_Ok", "stCode": 404, "errMsg": "Order not found"}

        if self.orders[order_id]["status"] != "OPEN":
            return {"stat": "Not_Ok", "stCode": 400, "errMsg": "Only OPEN orders can be cancelled"}

        self.orders[order_id]["status"] = "CANCELLED"
        logger.info(f"🔫 Order cancelled: {order_id}")
        return {"stat": "Ok", "stCode": 200, "result": "Order Cancelled"}

    # --- The Simulation Engine ---
    def process_candle(self, token: str, ohlc_candle: Dict) -> None:
        """
        Called every minute with the latest 1-min candle.
        Simulates order fills based on OHLC data.

        Args:
            token: Instrument token
            ohlc_candle: {'open': float, 'high': float, 'low': float, 'close': float, 'volume': int}
        """
        token = str(token)
        open_orders = [o for o in self.orders.values() if o["status"] == "OPEN"]

        for order in open_orders:
            if str(order["token"]) != token:
                continue

            fill_price = None
            fill_qty = order["qty"]

            # FILL LOGIC
            if order["type"] == "MKT":
                # Market orders fill at Open of the candle (instant execution)
                fill_price = ohlc_candle["open"]
                logger.debug(f"📍 MKT fill @ open: {fill_price}")

            elif order["type"] == "L":
                # Limit orders fill if price touched
                if order["side"] == "B":  # Limit Buy
                    # If Candle Low is below Limit Price, filled
                    if ohlc_candle["low"] <= order["price"]:
                        # Fill at Limit Price (aggressive) or better
                        fill_price = min(order["price"], ohlc_candle["open"])
                        logger.debug(f"📍 BUY Limit fill @ {fill_price}")

                elif order["side"] == "S":  # Limit Sell
                    # If Candle High is above Limit Price, filled
                    if ohlc_candle["high"] >= order["price"]:
                        # Fill at Limit Price (aggressive) or better
                        fill_price = max(order["price"], ohlc_candle["open"])
                        logger.debug(f"📍 SELL Limit fill @ {fill_price}")

            # Execute fill if conditions met
            if fill_price is not None:
                self._execute_fill(order, fill_qty, fill_price)

    def _execute_fill(self, order: Dict, qty: int, price: float) -> None:
        """
        Executes order fill and updates position.

        Args:
            order: Order dict
            qty: Quantity filled
            price: Fill price
        """
        order["status"] = "TRADED"
        order["filled_qty"] = qty
        order["avg_price"] = price

        token = order["token"]

        # Initialize position if needed
        if token not in self.positions:
            self.positions[token] = Position()

        pos = self.positions[token]

        # Calculate realized PnL if closing position
        realized_pnl = 0.0

        if order["side"] == "B":
            qty_signed = qty
        else:
            qty_signed = -qty

        # Update position
        if pos.qty * qty_signed <= 0:
            # Opposite sign: closing position
            realized_pnl = pos.close_partial(qty, price)
            self.total_realized_pnl += realized_pnl
        else:
            # Same sign: scaling in
            pos.add(qty_signed, price)

        # Update balance (simplified - actual margin calculation is complex)
        self.balance -= qty * price  # Deduct from balance

        # Add to ledger (keep last N trades)
        trade_record = {
            "order_id": order["nOrdNo"],
            "token": token,
            "side": "BUY" if order["side"] == "B" else "SELL",
            "qty": qty,
            "price": price,
            "timestamp": datetime.now(),
            "realized_pnl": realized_pnl,
        }
        self.ledger.append(trade_record)

        # Trim ledger if too large
        if len(self.ledger) > self.ledger_max_size:
            self.ledger.pop(0)

        logger.info(
            f"🔴 VIRTUAL FILL: {order['side']} {qty} @ {price:.2f} | "
            f"Realized PnL: {realized_pnl:+.2f} | "
            f"Pos: {pos.qty if pos.qty != 0 else 'FLAT'}"
        )

    def get_positions(self) -> Dict:
        """
        Returns current positions in Kotak Neo format.

        Returns:
            {'stat': 'Ok', 'data': [{'token': '123', 'quantity': 25, 'avg_price': 1500.0, ...}]}
        """
        positions_data = []

        for token, pos in self.positions.items():
            if pos.qty != 0:  # Only non-zero positions
                positions_data.append(
                    {
                        "instrumentToken": token,
                        "quantity": pos.qty,
                        "avgPrice": pos.avg_entry_price,
                        "netQty": pos.qty,
                        "netValue": pos.qty * pos.current_price,
                        "unrealizedPnl": pos.get_unrealized_pnl(pos.current_price),
                    }
                )

        return {"stat": "Ok", "data": positions_data}

    def get_pnl_summary(self) -> Dict:
        """Returns P&L summary."""
        unrealized_pnl = sum(pos.get_unrealized_pnl(pos.current_price) for pos in self.positions.values())

        return {
            "total_realized_pnl": self.total_realized_pnl,
            "total_unrealized_pnl": unrealized_pnl,
            "net_pnl": self.total_realized_pnl + unrealized_pnl,
            "current_balance": self.balance,
            "return_pct": ((self.balance - self.initial_balance) / self.initial_balance) * 100,
        }
