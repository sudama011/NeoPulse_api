"""Test doubles."""

import itertools
from typing import Optional

from app.broker.base import Broker, BrokerUnavailable, OrderRejected
from app.domain.models import BrokerOrder, BrokerPosition, OrderRequest, OrderStatus
from app.notifications.notifier import Notifier


class FakeBroker(Broker):
    """Scriptable broker: orders rest OPEN until the test fills/cancels them."""

    is_paper = False

    def __init__(self):
        super().__init__()
        self.book: dict[str, BrokerOrder] = {}
        self.requests: list[OrderRequest] = []
        self.broker_positions: list[BrokerPosition] = []
        self.margin: Optional[float] = 1_000_000.0
        self.fail_next: Optional[Exception] = None
        self.place_but_raise = False  # simulate: order reaches broker but the response is lost
        self.auto_fill_price: Optional[float] = None
        self._ids = itertools.count(1)

    async def place_order(self, req: OrderRequest) -> str:
        self.requests.append(req)
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        oid = f"B{next(self._ids)}"
        self.book[oid] = BrokerOrder(oid, OrderStatus.OPEN, 0, 0.0, req.qty, req.client_id, req.token)
        if self.auto_fill_price is not None:
            self.fill(oid, req.qty, self.auto_fill_price)
        if self.place_but_raise:
            raise BrokerUnavailable("timeout after send")
        return oid

    def fill(self, oid: str, filled_qty: int, avg_price: float) -> BrokerOrder:
        o = self.book[oid]
        status = OrderStatus.FILLED if filled_qty >= o.qty else OrderStatus.OPEN
        self.book[oid] = BrokerOrder(oid, status, filled_qty, avg_price, o.qty, o.client_id, o.token)
        return self.book[oid]

    def reject(self, oid: str, message: str = "RMS reject") -> None:
        o = self.book[oid]
        self.book[oid] = BrokerOrder(oid, OrderStatus.REJECTED, 0, 0.0, o.qty, o.client_id, o.token, message)

    async def cancel_order(self, broker_order_id: str) -> None:
        o = self.book[broker_order_id]
        if not o.status.is_terminal:
            self.book[broker_order_id] = BrokerOrder(
                o.broker_order_id, OrderStatus.CANCELLED, o.filled_qty, o.avg_price, o.qty, o.client_id, o.token
            )

    async def order_book(self) -> list[BrokerOrder]:
        return list(self.book.values())

    async def positions(self) -> list[BrokerPosition]:
        return list(self.broker_positions)

    async def available_margin(self) -> Optional[float]:
        return self.margin

    def last_order_id(self) -> str:
        return list(self.book)[-1]


class RecordingNotifier(Notifier):
    def __init__(self):
        super().__init__(None, None)
        self.messages: list[tuple[str, str]] = []

    def send(self, message: str, level: str = "INFO") -> None:
        self.messages.append((level, message))


__all__ = ["FakeBroker", "RecordingNotifier", "OrderRejected", "BrokerUnavailable"]
