"""
Persistence model.

Recovery relies on three tables:
- strategy_instance: what should be running, its params, net position and strategy state.
- orders: write-ahead log of every order (saved BEFORE it is sent to the broker).
- fills: every execution increment; daily P&L and the kill switch are derived from it.
"""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Instrument(Base):
    __tablename__ = "instrument"

    token: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trading_symbol: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(64))
    name: Mapped[Optional[str]] = mapped_column(String(128))
    segment: Mapped[Optional[str]] = mapped_column(String(16))
    series: Mapped[Optional[str]] = mapped_column(String(16))
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    tick_size: Mapped[float] = mapped_column(Numeric(10, 4), default=0.05)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class StrategyInstance(Base):
    __tablename__ = "strategy_instance"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    strategy_type: Mapped[str] = mapped_column(String(50))
    symbol: Mapped[str] = mapped_column(String(64))
    token: Mapped[str] = mapped_column(String(20), index=True)
    segment: Mapped[str] = mapped_column(String(16), default="nse_cm")
    product: Mapped[str] = mapped_column(String(8), default="MIS")
    timeframe: Mapped[int] = mapped_column(Integer, default=5)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # Capital & risk per instance
    capital: Mapped[float] = mapped_column(Float)
    risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=0.01)
    leverage: Mapped[float] = mapped_column(Float, default=1.0)

    # ACTIVE | STOPPED | DISABLED (auto-disabled after repeated errors)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    status_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Runtime state (survives restarts)
    position_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # At most one ACTIVE strategy per instrument: keeps broker reconciliation unambiguous.
        Index("uq_active_instance_per_token", "token", unique=True, postgresql_where=text("status = 'ACTIVE'")),
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # client id, sent as broker tag
    instance_id: Mapped[str] = mapped_column(ForeignKey("strategy_instance.id"), index=True)
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    symbol: Mapped[str] = mapped_column(String(64))
    token: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(Integer)
    order_type: Mapped[str] = mapped_column(String(6))
    product: Mapped[str] = mapped_column(String(8))
    price: Mapped[float] = mapped_column(Float, default=0.0)
    trigger_price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(12), index=True)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[Optional[str]] = mapped_column(String(64))  # why the strategy sent it
    message: Mapped[Optional[str]] = mapped_column(Text)  # broker / rejection message
    is_paper: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FillRecord(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    instance_id: Mapped[str] = mapped_column(ForeignKey("strategy_instance.id"), index=True)
    token: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    charges: Mapped[float] = mapped_column(Float, default=0.0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class KeyValue(Base):
    """Small durable flags (e.g. manual kill switch) keyed by name."""

    __tablename__ = "kv_store"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


__all__ = ["Base", "Instrument", "StrategyInstance", "Order", "FillRecord", "KeyValue"]
