import logging
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.data.master import master_data

logger = logging.getLogger("RequestSchema")


class StartRequest(BaseModel):
    capital: float = Field(default=100000.0, gt=0, le=10_000_000, description="Allocated Trading Capital")
    symbols: list[str] = Field(..., min_length=1, max_length=50)
    strategy: Literal["GENERIC", "MOMENTUM", "MEAN_REVERSION", "ORB", "RULE_ENGINE"] = "MOMENTUM"
    leverage: float = Field(default=1.0, ge=1.0, le=5.0)

    # Risk Limits
    max_daily_loss: float = Field(default=1000.0, gt=0)
    max_concurrent_trades: int = Field(default=3, ge=1, le=20)

    # Sizing Configuration
    sizing_method: Literal["FIXED_RISK", "FIXED_CAPITAL", "MARTINGALE", "ANTI_MARTINGALE"] = "FIXED_RISK"
    risk_per_trade_pct: float = Field(default=0.01, gt=0.0, le=0.10, description="Risk % per trade (0.01 = 1%)")

    # Generic params bags
    risk_params: dict = Field(default_factory=dict)
    strategy_params: dict = Field(default_factory=dict)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: list[str]) -> list[str]:
        """Ensures symbols exist in the master data."""
        # Note: This requires master_data to be loaded.
        # If running unit tests without master data, you might need to mock this.
        for symbol in v:
            if not master_data.get_token(symbol):
                raise ValueError(f"Symbol '{symbol}' not found in master data")
        return v

    @model_validator(mode="after")
    def validate_risk_limits(self) -> "StartRequest":
        """
        Cross-field validation: Checks if Max Loss exceeds 10% of Capital.
        This runs AFTER all individual fields are validated.
        """
        # Calculate max allowed loss based on the provided capital
        max_allowed_loss = self.capital * 0.10

        if self.max_daily_loss > max_allowed_loss:
            raise ValueError(
                f"Max daily loss (₹{self.max_daily_loss}) cannot exceed 10% of capital (₹{max_allowed_loss:.2f})"
            )

        return self
