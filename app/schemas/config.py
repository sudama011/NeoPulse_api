from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ProductType(str, Enum):
    MIS = "MIS"  # Intraday (Auto Square-off)
    CNC = "CNC"  # Delivery (Carry forward)


class StrategyType(str, Enum):
    MACD_VOLUME = "MACD_VOLUME"
    MOMENTUM = "MOMENTUM"
    GENERIC = "GENERIC"
    RULE_ENGINE = "RULE_ENGINE"


class StrategyConfig(BaseModel):
    instance_id: str  # e.g., "rel_macd_01"
    symbol: str  # "RELIANCE-EQ"
    token: str  # "2885"
    strategy_type: StrategyType
    timeframe_minutes: int = 5  # 1, 5, 15
    product: ProductType = ProductType.MIS
    params: Dict[str, Any] = Field(default_factory=dict)


class BacktestRequest(BaseModel):
    """Request schema for running a backtest."""
    symbol: str = Field(..., description="Stock symbol, e.g. RELIANCE")
    strategy: str = Field(default="MACD_VOLUME", description="Strategy name from registry")
    days: int = Field(default=30, ge=1, le=365, description="Number of days of history")
    interval: str = Field(default="5m", description="Candle interval: 1m, 5m, 15m, 1h, 1d")
    initial_capital: float = Field(default=100_000, gt=0)
    strategy_params: Dict[str, Any] = Field(default_factory=dict)


class BacktestResult(BaseModel):
    """Response schema for backtest results."""
    symbol: str
    strategy: str
    candles_processed: int = 0

    # Capital
    initial_capital: float
    final_equity: float = 0.0
    total_return: float = 0.0
    return_pct: float = 0.0

    # Trade Stats
    total_orders: int = 0
    round_trips: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0

    # P&L
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: Any = 0.0  # Can be "∞"
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    expectancy: float = 0.0

    # Risk
    max_drawdown_pct: float = 0.0
    max_drawdown_value: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0

    # Error
    error: Optional[str] = None
