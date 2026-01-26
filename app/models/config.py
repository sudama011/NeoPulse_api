# app/models/config.py
from sqlalchemy import Column, DateTime, Float, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.models.base import Base


class SystemConfig(Base):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True)

    # Core Params
    capital = Column(Numeric(10, 2), default=0.0)
    leverage = Column(Integer, default=1)

    # Strategy Params
    strategy_name = Column(String)
    symbols = Column(JSONB)
    strategy_params = Column(JSONB)

    # Risk Params
    max_daily_loss = Column(Float)
    max_concurrent_trades = Column(Integer)
    risk_params = Column(JSONB)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
