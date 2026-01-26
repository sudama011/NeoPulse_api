from sqlalchemy import Column, Date, DateTime, Integer, Numeric, String
from sqlalchemy.sql import func

from app.models.base import Base


class InstrumentMaster(Base):
    __tablename__ = "instrument_master"

    token = Column(Integer, primary_key=True)
    trading_symbol = Column(String(50), index=True, nullable=False)
    symbol = Column(String(50), index=True, nullable=False)
    name = Column(String(100))
    isin = Column(String(20), index=True, nullable=True)
    exchange = Column(String(10), default="NSE")
    segment = Column(String(10))
    series = Column(String(10), nullable=True)
    instrument_type = Column(String(20), nullable=True)
    option_type = Column(String(5), nullable=True)

    lot_size = Column(Integer, default=1)

    tick_size = Column(Numeric(10, 2), default=0.05)
    freeze_qty = Column(Integer, nullable=True)
    upper_band = Column(Numeric(10, 2), nullable=True)
    lower_band = Column(Numeric(10, 2), nullable=True)

    expiry_date = Column(Date, nullable=True)
    strike_price = Column(Numeric(10, 2), default=0.0)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Instrument {self.trading_symbol} ({self.token})>"
