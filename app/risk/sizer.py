import math
import logging
from app.risk.models import PositionConfig

logger = logging.getLogger("PositionSizer")

class PositionSizer:
    """
    Calculates the exact quantity to trade.
    Enforces 'Ruination Risk' protection.
    """

    def __init__(self, config: PositionConfig):
        self.config = config

    def calculate_qty(self, capital: float, entry_price: float, stop_loss_price: float) -> int:
        """
        Determines quantity based on Risk % logic.
        Formula: Qty = (Capital * Risk%) / (Entry - SL)
        """
        if entry_price <= 0 or stop_loss_price <= 0:
            logger.error("❌ Invalid Entry/SL prices for sizing.")
            return 0

        if self.config.method == "FIXED_RISK":
            # 1. Calculate Risk Per Share
            risk_per_share = abs(entry_price - stop_loss_price)
            if risk_per_share == 0:
                logger.warning("⚠️ Entry equals SL! Cannot calculate size.")
                return 0

            # 2. Calculate Total Risk Amount (e.g., 1% of ₹1 Lakh = ₹1000)
            risk_amount = capital * self.config.risk_per_trade_pct
            
            # 3. Derive Quantity
            raw_qty = risk_amount / risk_per_share
            
            # 4. Cap by Max Leverage/Capital
            # Ensure we don't exceed available buying power
            max_buying_power = capital * self.config.leverage
            max_qty_by_capital = max_buying_power / entry_price
            
            final_qty = min(raw_qty, max_qty_by_capital)
            
            qty = math.floor(final_qty)
            
            logger.info(
                f"🧮 Sizing: Risk ₹{risk_amount:.2f} | Risk/Share ₹{risk_per_share:.2f} | "
                f"Qty {qty} (Cap {math.floor(max_qty_by_capital)})"
            )
            return qty

        elif self.config.method == "FIXED_CAPITAL":
            # Simple: Allocation / Price
            # e.g., Put ₹25,000 in every trade
            allocation = capital * 0.25 # 25% allocation example
            return math.floor(allocation / entry_price)

        return 0