from sqlalchemy import Column, Integer, String, DECIMAL, DateTime, BigInteger, Date
from sqlalchemy.sql import func
from app.models.base import Base

class InstrumentMaster(Base):
    __tablename__ = "instrument_master"

    instrument_token = Column(Integer, primary_key=True)  # Kotak Token
    exchange_token = Column(String(20))
    trading_symbol = Column(String(50), index=True, nullable=False)
    name = Column(String(100))
    lot_size = Column(Integer, default=1)
    tick_size = Column(DECIMAL(10, 2), default=0.05)
    freeze_qty = Column(Integer)
    segment = Column(String(10))  # NSE_CM, NSE_FO
    
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

# Note: Market Ticks (High Frequency) are often better managed via raw SQL 
# or TimescaleDB hypertables, but we can define a model for basic ORM usage.
class MarketTick(Base):
    __tablename__ = "market_ticks"
    
    # Composite PK handled by TimescaleDB usually, but for ORM:
    tick_time = Column(DateTime(timezone=True), primary_key=True)
    token = Column(Integer, primary_key=True)
    ltp = Column(DECIMAL(10, 2))
    volume = Column(BigInteger)