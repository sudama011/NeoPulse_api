import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.data.master import master_data

logger = logging.getLogger("RequestSchema")


class StrategyConfig(BaseModel):
    """
    Configuration for a single strategy instance.
    Each strategy runs on one symbol with its own risk parameters.
    """

    symbol: str = Field(..., description="Trading symbol (e.g., RELIANCE, TCS)")
    strategy_type: str = Field(..., description="Strategy name from registry (e.g., MACD_VOLUME, GENERIC)")

    # Risk Parameters (per-strategy)
    leverage: float = Field(default=1.0, ge=1.0, le=5.0)
    sizing_method: Literal["FIXED_RISK", "FIXED_CAPITAL", "MARTINGALE", "ANTI_MARTINGALE"] = "FIXED_RISK"
    risk_per_trade_pct: float = Field(default=0.01, gt=0.0, le=0.10, description="Risk % per trade (0.01 = 1%)")

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """Ensures symbol exists in master data."""
        if not master_data.get_token(v):
            raise ValueError(f"Symbol '{v}' not found in master data")
        return v


class StartRequest(BaseModel):
    """
    Start one or more strategies.
    If session exists, adds to it. If not, creates new session.
    """

    # Session-level settings (only used if creating new session)
    capital: Optional[float] = Field(default=100000.0, gt=0, le=10_000_000, description="Allocated Trading Capital")
    max_daily_loss: Optional[float] = Field(default=1000.0, gt=0, description="Max loss before stopping all strategies")
    max_concurrent_trades: Optional[int] = Field(default=3, ge=1, le=20, description="Max concurrent positions")

    # Strategy configurations
    strategies: list[StrategyConfig] = Field(
        ..., min_length=1, max_length=50, description="List of strategies to start"
    )

    @model_validator(mode="after")
    def validate_risk_limits(self) -> "StartRequest":
        """Validate max_daily_loss doesn't exceed 10% of capital."""
        if self.capital and self.max_daily_loss:
            max_allowed_loss = self.capital * 0.10
            if self.max_daily_loss > max_allowed_loss:
                raise ValueError(
                    f"Max daily loss (₹{self.max_daily_loss}) cannot exceed 10% of capital (₹{max_allowed_loss:.2f})"
                )
        return self


class StopRequest(BaseModel):
    """
    Stop strategies.
    If no filters provided, stops all strategies.
    """

    symbol: Optional[str] = Field(default=None, description="Stop all strategies for this symbol")
    strategy_instance_id: Optional[str] = Field(default=None, description="Stop specific strategy instance by ID")
    stop_all: bool = Field(default=False, description="Stop all active strategies")
