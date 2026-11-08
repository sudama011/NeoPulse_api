"""
Core domain types shared by every layer (broker, OMS, portfolio, strategies, backtest).

These are plain value objects with no I/O, so they can be used anywhere without
creating import cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Optional


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(StrEnum):
    MARKET = "MKT"
    LIMIT = "L"
    STOP_LIMIT = "SL"
    STOP_MARKET = "SL-M"


class ProductType(StrEnum):
    MIS = "MIS"  # Intraday, broker auto square-off
    CNC = "CNC"  # Delivery
    NRML = "NRML"  # F&O carry forward


class OrderStatus(StrEnum):
    PENDING = "PENDING"  # Persisted locally, not yet acknowledged by broker
    UNKNOWN = "UNKNOWN"  # Send outcome uncertain (network error); resolved by reconciliation
    OPEN = "OPEN"  # Live at the exchange (may be partially filled)
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"  # May carry a partial fill
    REJECTED = "REJECTED"
    FAILED = "FAILED"  # Never reached the broker

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.FAILED}


@dataclass(frozen=True, slots=True)
class Tick:
    token: str
    ltp: float
    ts: datetime  # exchange (or receive) time, tz-aware IST
    volume: int = 0  # cumulative day volume
    symbol: Optional[str] = None


@dataclass(slots=True)
class Candle:
    start: datetime  # tz-aware IST, candle open time
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_id: str  # our id; sent to the broker as the order tag
    symbol: str  # broker trading symbol, e.g. RELIANCE-EQ
    token: str
    side: Side
    qty: int
    order_type: OrderType = OrderType.MARKET
    product: ProductType = ProductType.MIS
    price: float = 0.0
    trigger_price: float = 0.0
    exchange_segment: str = "nse_cm"


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    """Snapshot of one order as the broker currently sees it. Quantities are cumulative."""

    broker_order_id: str
    status: OrderStatus
    filled_qty: int
    avg_price: float
    qty: int = 0
    client_id: Optional[str] = None  # our tag echoed back
    token: Optional[str] = None
    message: Optional[str] = None


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    token: str
    symbol: str
    product: str
    net_qty: int
    avg_price: float


@dataclass(frozen=True, slots=True)
class Fill:
    """An execution increment attributed to one of our orders."""

    order_id: str
    instance_id: str
    token: str
    side: Side
    qty: int
    price: float
    ts: datetime
    realized_pnl: float = 0.0
    charges: float = 0.0


@dataclass(slots=True)
class PositionState:
    """Signed net position with average cost. Pure math, shared by live and backtest."""

    qty: int = 0
    avg_price: float = 0.0
    realized_pnl: float = field(default=0.0)

    def apply(self, side: Side, qty: int, price: float) -> float:
        """Applies a fill and returns the realized P&L it produced (before charges)."""
        if qty <= 0:
            return 0.0

        signed = side.sign * qty
        realized = 0.0

        if self.qty == 0 or (self.qty > 0) == (signed > 0):
            new_qty = self.qty + signed
            self.avg_price = (abs(self.qty) * self.avg_price + qty * price) / abs(new_qty)
            self.qty = new_qty
        else:
            closing = min(qty, abs(self.qty))
            direction = 1 if self.qty > 0 else -1
            realized = closing * (price - self.avg_price) * direction
            self.qty += signed
            if self.qty == 0:
                self.avg_price = 0.0
            elif qty > closing:  # flipped through zero: remainder opens at fill price
                self.avg_price = price

        self.realized_pnl += realized
        return realized

    def unrealized(self, mark: float) -> float:
        return (mark - self.avg_price) * self.qty if self.qty else 0.0
