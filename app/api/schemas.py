from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import ProductType


class CreateStrategyRequest(BaseModel):
    strategy_type: str = Field(..., examples=["MACD_VOLUME"])
    symbol: str = Field(..., examples=["RELIANCE"], description="Trading symbol, e.g. RELIANCE or RELIANCE-EQ")
    capital: float = Field(..., gt=0, description="Capital allocated to this instance (Rs)")
    params: dict[str, Any] = Field(default_factory=dict)
    timeframe: int = Field(5, description="Candle size in minutes: 1, 3, 5, 10, 15, 30, 60")
    product: ProductType = ProductType.MIS
    risk_per_trade_pct: float = Field(0.01, gt=0, le=1.0)
    leverage: float = Field(1.0, ge=1.0, le=5.0)
    name: Optional[str] = None


class BulkCreateRequest(BaseModel):
    """One instance per symbol; pair with MAX_OPEN_POSITIONS to cap concurrent positions."""

    strategy_type: str = Field(..., examples=["ORB"])
    symbols: list[str] = Field(..., min_length=1, max_length=200)
    capital_per_symbol: float = Field(..., gt=0)
    params: dict[str, Any] = Field(default_factory=dict)
    timeframe: int = 15
    product: ProductType = ProductType.MIS
    risk_per_trade_pct: float = Field(1.0, gt=0, le=1.0)
    leverage: float = Field(1.0, ge=1.0, le=5.0)


class StopStrategyRequest(BaseModel):
    flatten: bool = Field(True, description="Close the open position before stopping")


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    strategy_type: str
    symbol: str
    product: str
    timeframe: int
    params: dict[str, Any]
    capital: float
    risk_per_trade_pct: float
    leverage: float
    status: str
    status_reason: Optional[str]
    position_qty: int
    avg_price: float
    state: dict[str, Any]
    running: bool = False
    start_error: Optional[str] = None
    day_pnl: Optional[float] = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    instance_id: str
    broker_order_id: Optional[str]
    symbol: str
    side: str
    qty: int
    order_type: str
    price: float
    status: str
    filled_qty: int
    avg_price: float
    reason: Optional[str]
    message: Optional[str]
    is_paper: bool
    created_at: datetime


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = "manual"
