r"""
Order Management System.

Lifecycle:  PENDING --(broker ack)--> OPEN --(fills)--> FILLED
                    \--(reject)--> REJECTED     \--> CANCELLED (possibly partially filled)
                    \--(transport error)--> UNKNOWN --(reconciled via tag)--> OPEN/FILLED/... or FAILED

Guarantees:
- Write-ahead: an order row exists in the DB before the broker sees it, and carries our
  client id as the broker tag, so after any crash it can be matched to the broker's book.
- Idempotent fills: fills are derived from the broker's *cumulative* filled qty, so the
  order feed and polling can both deliver the same update safely.
- Never auto-resends an order whose outcome is unknown (avoids duplicate exposure).
"""

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Optional, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.broker.base import Broker, BrokerUnavailable, OrderRejected
from app.core.clock import now_ist, start_of_day, today_ist
from app.domain.models import BrokerOrder, Fill, OrderRequest, OrderStatus, OrderType, ProductType, Side
from app.models import Order
from app.portfolio.book import OrderFillUpdate, PositionBook

logger = logging.getLogger("OMS")

UNKNOWN_RESOLVE_SECONDS = 30  # UNKNOWN/PENDING orders missing from the book after this are FAILED
MISSING_OPEN_SECONDS = 120  # OPEN orders missing from the book after this are CANCELLED


def new_client_id() -> str:
    return "NP" + uuid.uuid4().hex[:14].upper()


@dataclass
class OrderView:
    id: str
    instance_id: str
    symbol: str
    token: str
    side: Side
    qty: int
    order_type: OrderType
    product: ProductType
    price: float = 0.0
    trigger_price: float = 0.0
    segment: str = "nse_cm"
    reason: str = ""
    status: OrderStatus = OrderStatus.PENDING
    broker_order_id: Optional[str] = None
    filled_qty: int = 0
    avg_price: float = 0.0
    message: Optional[str] = None
    created_at: datetime = field(default_factory=now_ist)
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def remaining(self) -> int:
        return self.qty - self.filled_qty

    @property
    def age_seconds(self) -> float:
        return (now_ist() - self.created_at).total_seconds()

    @classmethod
    def from_row(cls, r: Order) -> "OrderView":
        return cls(
            id=r.id,
            instance_id=r.instance_id,
            symbol=r.symbol,
            token=r.token,
            side=Side(r.side),
            qty=r.qty,
            order_type=OrderType(r.order_type),
            product=ProductType(r.product),
            price=r.price or 0.0,
            trigger_price=r.trigger_price or 0.0,
            reason=r.reason or "",
            status=OrderStatus(r.status),
            broker_order_id=r.broker_order_id,
            filled_qty=r.filled_qty or 0,
            avg_price=r.avg_price or 0.0,
            message=r.message,
            created_at=r.created_at,
        )


class OrderListener(Protocol):
    async def on_order_update(self, order: OrderView) -> None: ...
    async def on_fill(self, fill: Fill) -> None: ...


class OrderManager:
    def __init__(
        self,
        broker: Broker,
        session_factory: async_sessionmaker,
        positions: PositionBook,
        limit_order_ttl: float = 60.0,
    ):
        self._broker = broker
        self._sf = session_factory
        self._positions = positions
        self._limit_ttl = limit_order_ttl
        self._open: dict[str, OrderView] = {}
        self._by_broker_id: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._listeners: dict[str, OrderListener] = {}
        self._events: asyncio.Queue[tuple[OrderView, Optional[Fill]]] = asyncio.Queue()
        self._dispatcher: Optional[asyncio.Task] = None
        self._cancel_requested: set[str] = set()
        broker.set_order_handler(self.on_broker_update)

    # ------------------------------------------------------------------ queries
    def add_listener(self, instance_id: str, listener: OrderListener) -> None:
        self._listeners[instance_id] = listener

    def remove_listener(self, instance_id: str) -> None:
        self._listeners.pop(instance_id, None)

    def open_orders(self, instance_id: Optional[str] = None) -> list[OrderView]:
        return [o for o in self._open.values() if instance_id is None or o.instance_id == instance_id]

    @property
    def has_open_orders(self) -> bool:
        return bool(self._open)

    # ------------------------------------------------------------------ commands
    async def submit(
        self,
        *,
        instance_id: str,
        symbol: str,
        token: str,
        side: Side,
        qty: int,
        order_type: OrderType = OrderType.MARKET,
        product: ProductType = ProductType.MIS,
        price: float = 0.0,
        trigger_price: float = 0.0,
        segment: str = "nse_cm",
        reason: str = "",
    ) -> OrderView:
        view = OrderView(
            id=new_client_id(),
            instance_id=instance_id,
            symbol=symbol,
            token=token,
            side=side,
            qty=qty,
            order_type=order_type,
            product=product,
            price=price,
            trigger_price=trigger_price,
            segment=segment,
            reason=reason[:64],
        )
        await self._insert(view)  # write-ahead
        self._open[view.id] = view

        req = OrderRequest(
            client_id=view.id,
            symbol=symbol,
            token=token,
            side=side,
            qty=qty,
            order_type=order_type,
            product=product,
            price=price,
            trigger_price=trigger_price,
            exchange_segment=segment,
        )
        logger.info("📤 %s %s %d %s %s @ %s [%s]", view.id, side, qty, symbol, order_type, price or "MKT", reason)
        try:
            broker_id = await self._broker.place_order(req)
        except OrderRejected as e:
            await self._finalize(view, OrderStatus.REJECTED, str(e))
            logger.error("⛔ Order %s rejected: %s", view.id, e)
        except (BrokerUnavailable, Exception) as e:  # outcome unknown: reconcile, never resend
            async with self._lock:
                view.status, view.message = OrderStatus.UNKNOWN, str(e)
                await self._persist(view)
            logger.error("❓ Order %s outcome unknown (%s); will reconcile with broker", view.id, e)
        else:
            async with self._lock:
                view.broker_order_id = broker_id
                if not view.status.is_terminal:
                    self._by_broker_id[broker_id] = view.id
                if view.status in (OrderStatus.PENDING, OrderStatus.UNKNOWN):
                    view.status = OrderStatus.OPEN
                if not view.status.is_terminal:
                    await self._persist(view)
        await self._notify(view)
        return view

    async def cancel(self, order_id: str) -> None:
        view = self._open.get(order_id)
        if view is None or not view.broker_order_id:
            return
        self._cancel_requested.add(order_id)
        try:
            await self._broker.cancel_order(view.broker_order_id)
            logger.info("🚫 Cancel requested for %s", order_id)
        except Exception as e:  # likely already filled/cancelled; polling settles it
            logger.warning("Cancel %s failed: %s", order_id, e)

    async def cancel_all(self, instance_id: Optional[str] = None) -> None:
        for view in self.open_orders(instance_id):
            await self.cancel(view.id)

    async def wait_until_idle(self, instance_id: str, timeout: float = 15.0) -> bool:
        """Waits until the instance has no open orders, actively polling the broker meanwhile."""
        deadline = time.monotonic() + timeout
        while self.open_orders(instance_id):
            if time.monotonic() >= deadline:
                return False
            await self.sync_with_broker()
            if self.open_orders(instance_id):
                await asyncio.sleep(1.0)
        return True

    # ------------------------------------------------------------------ broker updates
    async def on_broker_update(self, bo: BrokerOrder) -> None:
        fill = None
        async with self._lock:
            view = self._match(bo)
            if view is not None:
                fill = await self._apply(view, bo)
        if view is not None:
            await self._notify(view, fill)

    def _match(self, bo: BrokerOrder) -> Optional[OrderView]:
        if bo.client_id and bo.client_id in self._open:
            return self._open[bo.client_id]
        oid = self._by_broker_id.get(bo.broker_order_id)
        return self._open.get(oid) if oid else None

    async def _apply(self, view: OrderView, bo: BrokerOrder) -> Optional[Fill]:
        """Applies a broker snapshot and returns the new fill, if any. Must hold self._lock."""
        view.last_seen = time.monotonic()
        if not view.broker_order_id:
            view.broker_order_id = bo.broker_order_id
            self._by_broker_id[bo.broker_order_id] = view.id

        status = bo.status
        if status is OrderStatus.OPEN and view.status in (OrderStatus.PENDING, OrderStatus.UNKNOWN):
            logger.info("✅ Order %s acknowledged by broker (%s)", view.id, bo.broker_order_id)
        if bo.message:
            view.message = bo.message

        if bo.filled_qty > view.filled_qty:
            delta = bo.filled_qty - view.filled_qty
            price = self._derive_fill_price(view, bo, delta)
            view.filled_qty = bo.filled_qty
            view.avg_price = bo.avg_price or price
            view.status = status
            fill = await self._positions.record_fill(
                OrderFillUpdate(
                    order_id=view.id,
                    instance_id=view.instance_id,
                    token=view.token,
                    side=view.side,
                    product=view.product.value,
                    filled_qty=view.filled_qty,
                    avg_price=view.avg_price,
                    status=view.status.value,
                    broker_order_id=view.broker_order_id,
                    message=view.message,
                ),
                delta,
                price,
            )
            if status.is_terminal:
                self._untrack(view)
            return fill

        if status != view.status:
            view.status = status
            if status.is_terminal:
                self._untrack(view)
                if status is OrderStatus.REJECTED:
                    logger.error("⛔ Order %s rejected by exchange: %s", view.id, view.message)
            await self._persist(view)
        return None

    @staticmethod
    def _derive_fill_price(view: OrderView, bo: BrokerOrder, delta: int) -> float:
        if bo.avg_price > 0:
            incremental = (bo.avg_price * bo.filled_qty - view.avg_price * view.filled_qty) / delta
            if incremental > 0:
                return round(incremental, 4)
            return bo.avg_price
        return view.price

    # ------------------------------------------------------------------ reconciliation
    async def recover(self) -> None:
        """Loads unfinished orders from the DB and reconciles them against the broker."""
        async with self._sf() as db:
            rows = (
                (await db.execute(select(Order).where(Order.status.notin_([s.value for s in _TERMINAL]))))
                .scalars()
                .all()
            )

        today_start = start_of_day(today_ist())
        for row in rows:
            view = OrderView.from_row(row)
            if view.status is OrderStatus.PENDING:
                # Crashed between write-ahead and broker ack: outcome unknown, resolve via tag.
                view.status = OrderStatus.UNKNOWN
            if row.created_at < today_start:
                # DAY orders expire at the exchange overnight.
                await self._finalize(view, OrderStatus.CANCELLED, "expired (previous session)")
                continue
            self._open[view.id] = view
            if view.broker_order_id:
                self._by_broker_id[view.broker_order_id] = view.id

        logger.info("♻️ Recovering %d open orders", len(self._open))
        if self._open:
            await self.sync_with_broker()

    async def sync_with_broker(self) -> None:
        """Polls the broker order book and applies any changes. Safe to call at any time."""
        if not self._open:
            return
        try:
            book = await self._broker.order_book()
        except Exception as e:
            logger.warning("Order book poll failed: %s", e)
            return

        by_tag = {o.client_id: o for o in book if o.client_id}
        by_id = {o.broker_order_id: o for o in book}
        updated: list[tuple[OrderView, Optional[Fill]]] = []

        async with self._lock:
            for view in list(self._open.values()):
                bo = by_tag.get(view.id) or (by_id.get(view.broker_order_id) if view.broker_order_id else None)
                if bo is not None:
                    updated.append((view, await self._apply(view, bo)))
                elif view.status is OrderStatus.PENDING:
                    continue  # submit() is still in flight; it owns this order's next state
                elif view.status is OrderStatus.UNKNOWN:
                    if view.age_seconds > UNKNOWN_RESOLVE_SECONDS:
                        await self._finalize_locked(view, OrderStatus.FAILED, "not found at broker")
                        updated.append((view, None))
                elif time.monotonic() - view.last_seen > MISSING_OPEN_SECONDS:
                    view.last_seen = time.monotonic()  # report at most once per window
                    if self._broker.is_paper:  # paper book is in-memory and lost on restart
                        await self._finalize_locked(view, OrderStatus.CANCELLED, "missing from paper order book")
                        updated.append((view, None))
                    else:  # never assume a live order is dead: that risks double exposure
                        logger.error("Order %s (%s) missing from broker order book", view.id, view.broker_order_id)

        for view, fill in updated:
            await self._notify(view, fill)
        await self._cancel_stale_limits()

    async def _cancel_stale_limits(self) -> None:
        for view in list(self._open.values()):
            if (
                view.order_type is OrderType.LIMIT
                and view.status is OrderStatus.OPEN
                and view.age_seconds > self._limit_ttl
                and view.id not in self._cancel_requested
            ):
                logger.info("⌛ Limit order %s unfilled for %.0fs; cancelling", view.id, view.age_seconds)
                await self.cancel(view.id)

    # ------------------------------------------------------------------ persistence helpers
    async def _insert(self, view: OrderView) -> None:
        async with self._sf() as db, db.begin():
            db.add(
                Order(
                    id=view.id,
                    instance_id=view.instance_id,
                    symbol=view.symbol,
                    token=view.token,
                    side=view.side.value,
                    qty=view.qty,
                    order_type=view.order_type.value,
                    product=view.product.value,
                    price=view.price,
                    trigger_price=view.trigger_price,
                    status=view.status.value,
                    reason=view.reason,
                    is_paper=self._broker.is_paper,
                    created_at=view.created_at,
                )
            )

    async def _persist(self, view: OrderView) -> None:
        async with self._sf() as db, db.begin():
            await db.execute(
                update(Order)
                .where(Order.id == view.id)
                .values(
                    status=view.status.value,
                    broker_order_id=view.broker_order_id,
                    filled_qty=view.filled_qty,
                    avg_price=view.avg_price,
                    message=view.message,
                )
            )

    async def _finalize(self, view: OrderView, status: OrderStatus, message: str) -> None:
        async with self._lock:
            await self._finalize_locked(view, status, message)

    async def _finalize_locked(self, view: OrderView, status: OrderStatus, message: str) -> None:
        view.status, view.message = status, message
        self._untrack(view)
        await self._persist(view)

    def _untrack(self, view: OrderView) -> None:
        self._open.pop(view.id, None)
        self._cancel_requested.discard(view.id)
        if view.broker_order_id:
            self._by_broker_id.pop(view.broker_order_id, None)

    # ------------------------------------------------------------------ listeners
    async def _notify(self, view: OrderView, fill: Optional[Fill] = None) -> None:
        """
        Queues listener callbacks for the dispatcher task. Delivery is ordered and never
        re-enters the caller, so a strategy that is mid-`submit` (holding its own lock)
        cannot deadlock when the broker reports a fill synchronously.
        """
        if view.instance_id not in self._listeners:
            return
        if self._dispatcher is None or self._dispatcher.done():
            self._dispatcher = asyncio.create_task(self._dispatch(), name="oms-dispatcher")
        self._events.put_nowait((view, fill))

    async def _dispatch(self) -> None:
        while True:
            view, fill = await self._events.get()
            listener = self._listeners.get(view.instance_id)
            if listener is not None:
                if fill is not None:
                    await _safe(listener.on_fill(fill))
                await _safe(listener.on_order_update(view))
            self._events.task_done()

    async def drain(self) -> None:
        """Waits until all queued listener notifications have been delivered."""
        if self._dispatcher is not None and not self._dispatcher.done():
            await self._events.join()

    async def close(self) -> None:
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dispatcher


_TERMINAL = [s for s in OrderStatus if s.is_terminal]


async def _safe(awaitable: Awaitable[None]) -> None:
    try:
        await awaitable
    except Exception:
        logger.exception("Order listener failed")
