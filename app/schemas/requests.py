import logging

from pydantic import BaseModel, Field, field_validator

from app.services.strategy.manager import strategy_engine

logger = logging.getLogger("RequestSchema")


class StartRequest(BaseModel):
    capital: float = Field(..., gt=0, le=10_000_000, description="Allocated Trading Capital (₹1 to ₹1 Cr)")
    symbols: list[str] = Field(..., min_length=1, max_length=50, description="List of Trading Symbols")
    strategy: str = Field(default="MOMENTUM_TREND", description="Strategy name")
    leverage: float = Field(default=1.0, ge=1.0, le=5.0, description="Leverage factor")
    max_daily_loss: float = Field(default=1000.0, gt=0, description="Max daily loss in rupees")
    max_concurrent_trades: int = Field(default=3, ge=1, le=20, description="Max concurrent open trades")
    risk_params: dict = Field(default_factory=dict, description="Additional risk parameters")
    strategy_params: dict = Field(default_factory=dict, description="Strategy-specific parameters")

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: list[str]) -> list[str]:
        """Validate trading symbols - basic alphanumeric check."""
        valid_symbols = {
            "RELIANCE",
            "TCS",
            "INFY",
            "WIPRO",
            "BAJAJFINSV",
            "HINDUNILVR",
            "ICICIBANK",
            "SBIN",
            "LANDT",
            "AXISBANK",
            "MARUTI",
            "ASIANPAINT",
            "ULTRAMARINE",
            "BRITANNIA",
            "NESTLEIND",
            "DRREDDY",
            "SUNPHARMA",
            "CIPLA",
            "LUPIN",
            "INDIGO",
            # Add more as needed
        }

        for symbol in v:
            if not isinstance(symbol, str):
                raise ValueError(f"Symbol must be string, got {type(symbol)}")
            if len(symbol) < 2 or len(symbol) > 20:
                raise ValueError(f"Symbol '{symbol}' invalid length")
            if not symbol.replace("&", "").replace("-", "").isalnum():
                raise ValueError(f"Symbol '{symbol}' contains invalid characters")
            # Optional: whitelist check (commented out for flexibility)
            # if symbol not in valid_symbols:
            #     raise ValueError(f"Symbol '{symbol}' not in supported list")

        return v

    @field_validator("max_daily_loss")
    @classmethod
    def validate_loss(cls, v: float, info) -> float:
        """Validate loss doesn't exceed 20% of capital."""
        if "capital" in info.data:
            max_allowed_loss = info.data["capital"] * 0.20
            if v > max_allowed_loss:
                raise ValueError(f"Max daily loss (₹{v}) cannot exceed 20% of capital " f"(₹{max_allowed_loss:.2f})")
        return v

    @field_validator("leverage")
    @classmethod
    def validate_leverage(cls, v: float) -> float:
        """Validate leverage for paper trading."""
        if v > 2.0:
            logger.warning(f"⚠️ High leverage requested: {v}x")
        return v

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        """Validate strategy exists."""
        valid_strategies = list(strategy_engine.STRATEGY_MAP.keys())
        if v not in valid_strategies:
            raise ValueError(f"Strategy '{v}' not found. Available: {valid_strategies}")
        return v
