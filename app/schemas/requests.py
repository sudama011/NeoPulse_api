import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.data.master import master_data
from app.strategy.engine import strategy_engine

logger = logging.getLogger("RequestSchema")


class StartRequest(BaseModel):
    capital: float = Field(default=100000.0, gt=0, le=10_000_000, description="Allocated Trading Capital")
    symbols: list[str] = Field(..., min_length=1, max_length=50)
    strategy: Literal["GENERIC", "MOMENTUM", "MEAN_REVERSION", "ORB", "RULE_ENGINE"] = "MOMENTUM"
    leverage: float = Field(default=1.0, ge=1.0, le=5.0)

    # Risk Limits
    max_daily_loss: float = Field(default=1000.0, gt=0)
    max_concurrent_trades: int = Field(default=3, ge=1, le=20)

    # NEW: Sizing Configuration
    sizing_method: Literal["FIXED_RISK", "FIXED_CAPITAL", "MARTINGALE", "ANTI_MARTINGALE"] = "FIXED_RISK"
    risk_per_trade_pct: float = Field(default=0.01, gt=0.0, le=0.10, description="Risk % per trade (0.01 = 1%)")

    # Generic params bags
    risk_params: dict = Field(default_factory=dict)
    strategy_params: dict = Field(default_factory=dict)

    @field_validator("max_daily_loss")
    @classmethod
    def validate_loss(cls, v: float) -> float:
        """Validate loss doesn't exceed 10% of capital."""
        max_allowed_loss = cls.capital * 0.10
        if v > max_allowed_loss:
            raise ValueError(f"Max daily loss (₹{v}) cannot exceed 10% of capital " f"(₹{max_allowed_loss:.2f})")
        return v

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: list[str]) -> list[str]:
        for symbol in v:
            if not master_data.get_token(symbol):
                raise ValueError(f"Symbol '{symbol}' not found in master data")
        return v
