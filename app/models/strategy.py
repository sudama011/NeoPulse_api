"""
Models for Strategy state persistence and Trade P&L tracking.
"""

from sqlalchemy import (
    DECIMAL,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func, text

from app.models.base import Base


class StrategyInstance(Base):
    """
    Persists each running strategy instance.
    Allows the engine to restore state after a restart.
    """

    __tablename__ = "strategy_instance"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    instance_name = Column(String(100), unique=True, nullable=False, index=True)

    strategy_type = Column(String(50), nullable=False)  # Registry key, e.g. "MACD_VOLUME"
    symbol = Column(String(50), nullable=False)
    token = Column(String(20), nullable=False)

    # Configuration snapshot
    params = Column(JSONB, default={})

    # Runtime state
    is_active = Column(Boolean, default=True)
    position = Column(Integer, default=0)
    avg_price = Column(DECIMAL(10, 2), default=0.0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<StrategyInstance {self.instance_name} ({self.strategy_type})>"


class TradeLog(Base):
    """
    Paired trade log: each row = one complete round-trip (Entry + Exit).
    This powers the performance analytics (Win Rate, Profit Factor, etc.).
    """

    __tablename__ = "trade_log"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))

    strategy_instance = Column(String(100), index=True, nullable=False)
    symbol = Column(String(50), nullable=False)

    # Entry
    entry_side = Column(String(4), nullable=False)  # BUY / SELL
    entry_price = Column(DECIMAL(10, 2), nullable=False)
    entry_qty = Column(Integer, nullable=False)
    entry_time = Column(DateTime(timezone=True), nullable=False)
    entry_tag = Column(String(50))

    # Exit
    exit_price = Column(DECIMAL(10, 2), nullable=True)
    exit_time = Column(DateTime(timezone=True), nullable=True)
    exit_tag = Column(String(50))

    # P&L
    pnl = Column(Float, nullable=True)  # Realized P&L
    pnl_pct = Column(Float, nullable=True)  # Return %
    holding_duration_sec = Column(Integer, nullable=True)

    # Metadata
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BacktestRun(Base):
    """
    Persists backtest run results for comparison and analysis.
    """

    __tablename__ = "backtest_run"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))

    strategy_type = Column(String(50), nullable=False)
    symbol = Column(String(50), nullable=False)
    timeframe = Column(String(10), default="5m")
    period_days = Column(Integer, default=30)

    # Capital
    initial_capital = Column(Float, nullable=False)
    final_equity = Column(Float)

    # Core Metrics
    total_return_pct = Column(Float)
    max_drawdown_pct = Column(Float)
    sharpe_ratio = Column(Float)
    sortino_ratio = Column(Float)
    profit_factor = Column(Float)
    win_rate = Column(Float)
    total_trades = Column(Integer)

    # Params snapshot
    strategy_params = Column(JSONB, default={})

    # Full metrics blob
    full_report = Column(JSONB, default={})

    created_at = Column(DateTime(timezone=True), server_default=func.now())

