from pydantic import BaseModel, Field


class RiskConfig(BaseModel):
    """
    The Constitution: Rules that cannot be broken.
    """

    max_daily_loss: float = Field(..., description="Stop trading if loss hits this amount (e.g., ₹2000)")
    max_capital_per_trade: float = Field(..., description="Max capital allocated to a single trade")
    max_open_trades: int = Field(3, description="Max concurrent positions to avoid over-exposure")

    # Advanced Guardrails
    max_drawdown_pct: float = Field(0.05, description="Stop if equity drops 5% from peak")
    kill_switch_active: bool = False


class PositionConfig(BaseModel):
    """
    Sizing Rules: How much to buy?
    """

    method: str = "FIXED_RISK"  # FIXED_RISK, FIXED_CAPITAL, KELLY
    risk_per_trade_pct: float = 0.01  # Risk 1% of capital per trade
    leverage: float = 1.0  # 1x for Equity, higher for F&O
