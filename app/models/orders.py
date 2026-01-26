from sqlalchemy import DECIMAL, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func, text

from app.models.base import Base


class OrderLedger(Base):
    __tablename__ = "order_ledger"

    internal_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    exchange_id = Column(String(50), index=True, nullable=True)

    token = Column(Integer, ForeignKey("instrument_master.token"), nullable=False)

    transaction_type = Column(String(4))  # BUY / SELL
    order_type = Column(String(10))  # L, MKT, SL, SL-M
    product = Column(String(10))  # MIS / NRML

    quantity = Column(Integer, nullable=False)
    price = Column(DECIMAL(10, 2))
    trigger_price = Column(DECIMAL(10, 2), nullable=True)

    status = Column(String(20), default="PENDING_LOCAL", index=True)
    rejection_reason = Column(Text, nullable=True)

    strategy_id = Column(String(50))

    # Audit Trail
    raw_request = Column(JSONB)
    raw_response = Column(JSONB)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
