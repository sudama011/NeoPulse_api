"""
Paper broker: simulates the exchange against live prices.

- Market orders fill immediately at the last traded price +/- slippage.
- Limit orders fill when the last price crosses the limit (checked whenever the OMS polls).
- Stop orders trigger when the last price crosses the trigger.
State is in memory; positions survive restarts through the PositionBook in the database.
"""

import itertools
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from app.broker.base import Broker, OrderRejected
from app.domain.models import (
    BrokerOrder,
    BrokerPosition,
    OrderRequest,
    OrderStatus,
    OrderType,
    PositionState,
    Side,
)

logger = logging.getLogger("PaperBroker")

PriceSource = Callable[[str], Optional[float]]


@dataclass
class _PaperOrder:
    req: OrderRequest
    broker_id: str
    status: OrderStatus = OrderStatus.OPEN
    filled_qty: int = 0
    avg_price: float = 0.0
    message: Optional[str] = None

    def snapshot(self) -> BrokerOrder:
        return BrokerOrder(
            broker_order_id=self.broker_id,
            status=self.status,
            filled_qty=self.filled_qty,
            avg_price=self.avg_price,
            qty=self.req.qty,
            client_id=self.req.client_id,
            token=self.req.token,
            message=self.message,
        )


class PaperBroker(Broker):
    is_paper = True

    def __init__(self, price_source: PriceSource, slippage_bps: float = 2.0, starting_cash: float = 1_000_000.0):
        super().__init__()
        self._price = price_source
        self._slippage = slippage_bps / 10_000
        self._cash = starting_cash
        self._orders: dict[str, _PaperOrder] = {}
        self._positions: dict[str, PositionState] = {}
        self._symbols: dict[str, str] = {}
        self._ids = itertools.count(1)

    async def place_order(self, req: OrderRequest) -> str:
        if req.qty <= 0:
            raise OrderRejected("quantity must be positive")
        if req.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and req.price <= 0:
            raise OrderRejected("price must be positive for limit orders")
        if self._price(req.token) is None:
            raise OrderRejected(f"no market price for token {req.token}")

        order = _PaperOrder(req=req, broker_id=f"PAPER{next(self._ids):08d}")
        self._orders[order.broker_id] = order
        self._symbols[req.token] = req.symbol
        self._try_fill(order)
        logger.info("📝 [PAPER] %s %s %s %s -> %s", req.side, req.qty, req.symbol, req.order_type, order.status)
        await self._emit(order.snapshot())
        return order.broker_id

    async def cancel_order(self, broker_order_id: str) -> None:
        order = self._orders.get(broker_order_id)
        if order and not order.status.is_terminal:
            order.status = OrderStatus.CANCELLED
            await self._emit(order.snapshot())

    async def order_book(self) -> list[BrokerOrder]:
        for order in self._orders.values():
            if not order.status.is_terminal:
                self._try_fill(order)
        return [o.snapshot() for o in self._orders.values()]

    async def positions(self) -> list[BrokerPosition]:
        return [
            BrokerPosition(token=t, symbol=self._symbols.get(t, t), product="MIS", net_qty=p.qty, avg_price=p.avg_price)
            for t, p in self._positions.items()
        ]

    async def available_margin(self) -> Optional[float]:
        return self._cash

    # ------------------------------------------------------------------ matching
    def _try_fill(self, order: _PaperOrder) -> None:
        req = order.req
        ltp = self._price(req.token)
        if ltp is None:
            return

        buy = req.side is Side.BUY
        if req.order_type in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT):
            triggered = ltp >= req.trigger_price if buy else ltp <= req.trigger_price
            if not triggered:
                return

        if req.order_type in (OrderType.MARKET, OrderType.STOP_MARKET):
            price = ltp * (1 + self._slippage) if buy else ltp * (1 - self._slippage)
        else:
            marketable = ltp <= req.price if buy else ltp >= req.price
            if not marketable:
                return
            price = min(req.price, ltp) if buy else max(req.price, ltp)

        price = round(price, 2)
        order.filled_qty = req.qty
        order.avg_price = price
        order.status = OrderStatus.FILLED
        self._positions.setdefault(req.token, PositionState()).apply(req.side, req.qty, price)
        self._cash -= req.side.sign * req.qty * price
