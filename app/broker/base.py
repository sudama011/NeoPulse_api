"""Broker port: the only interface the OMS / risk layers depend on."""

from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional

from app.domain.models import BrokerOrder, BrokerPosition, OrderRequest

OrderUpdateHandler = Callable[[BrokerOrder], Awaitable[None]]


class BrokerError(Exception):
    """Base broker failure."""


class OrderRejected(BrokerError):
    """Definitive: the order was NOT placed (validation, RMS, exchange reject)."""


class BrokerUnavailable(BrokerError):
    """Uncertain: the call failed in transit. The order may or may not exist at the broker."""


class Broker(ABC):
    is_paper: bool = False

    def __init__(self):
        self._order_handler: Optional[OrderUpdateHandler] = None

    def set_order_handler(self, handler: OrderUpdateHandler) -> None:
        """Push-style order updates (e.g. order WebSocket). Polling via order_book() is always the fallback."""
        self._order_handler = handler

    async def _emit(self, order: BrokerOrder) -> None:
        if self._order_handler is not None:
            await self._order_handler(order)

    async def start(self) -> None:
        """Start background resources (order feed, etc.)."""

    async def stop(self) -> None:
        """Release background resources."""

    @abstractmethod
    async def place_order(self, req: OrderRequest) -> str:
        """Returns the broker order id. Raises OrderRejected or BrokerUnavailable."""

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> None: ...

    @abstractmethod
    async def order_book(self) -> list[BrokerOrder]:
        """All of today's orders. Raises BrokerUnavailable on failure."""

    @abstractmethod
    async def positions(self) -> list[BrokerPosition]:
        """Today's net positions. Raises BrokerUnavailable on failure."""

    @abstractmethod
    async def available_margin(self) -> Optional[float]:
        """Free cash/margin, or None when unknown."""
