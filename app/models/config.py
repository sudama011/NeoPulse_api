# app/models/config.py
from sqlalchemy import Column, Integer, String, Float, JSON, DateTime
from datetime import datetime
from app.models.base import Base

class SystemConfig(Base):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True) # e.g., "current_state"
    
    # Core Params
    capital = Column(Float, default=0.0)
    leverage = Column(Float, default=1.0)
    
    # Strategy Params
    strategy_name = Column(String)
    symbols = Column(JSON) # List of strings ["RELIANCE", "INFY"]
    strategy_params = Column(JSON) # {"window": 15, ...}
    
    # Risk Params
    max_daily_loss = Column(Float)
    max_concurrent_trades = Column(Integer)
    risk_params = Column(JSON)

    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

# Add this to app/db/base.py imports so Alembic/InitDB sees it!