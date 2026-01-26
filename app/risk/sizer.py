import logging
import math
from app.risk.models import PositionConfig

logger = logging.getLogger("PositionSizer")

class PositionSizer:
    """
    Calculates the exact quantity to trade.
    Enforces 'Ruination Risk' protection and Instrument Specifics (Lot Size).
    """

    def __init__(self, config: PositionConfig):
        self.config = config

    def calculate_qty(self, capital: float, entry_price: float, sl_price: float, lot_size: int = 1, consecutive_losses: int = 0, consecutive_wins: int = 0) -> int:
        """
        Determines quantity based on Risk % logic and Lot constraints.
        Formula: Qty = Floor((Capital * Risk%) / (Entry - SL) / LotSize) * LotSize
        """
        if entry_price <= 0 or sl_price <= 0:
            logger.error("❌ Invalid Entry/SL prices for sizing.")
            return 0
        
        # Prevent division by zero if bad data passed
        lot_size = max(1, int(lot_size))

        qty = 0

        if self.config.method == "FIXED_RISK":
            # 1. Calculate Risk Per Share
            risk_per_share = abs(entry_price - sl_price)
            if risk_per_share == 0:
                logger.warning("⚠️ Entry equals SL! Cannot calculate size.")
                return 0

            # 2. Calculate Total Risk Amount
            risk_amount = capital * self.config.risk_per_trade_pct

            # 3. Derive Raw Quantity
            raw_qty = risk_amount / risk_per_share

            # 4. Cap by Max Leverage/Capital
            max_buying_power = capital * self.config.leverage
            max_qty_by_capital = max_buying_power / entry_price

            final_raw_qty = min(raw_qty, max_qty_by_capital)
            
            # 5. Apply Lot Size Rounding (Floor)
            # Example: Raw 63, Lot 25 -> 50 (2 Lots)
            qty = math.floor(final_raw_qty / lot_size) * lot_size

            logger.info(
                f"🧮 Sizing: Risk ₹{risk_amount:.2f} | Risk/Share ₹{risk_per_share:.2f} | "
                f"Raw {int(final_raw_qty)} -> Lot Adj {qty} (Lot {lot_size})"
            )

        elif self.config.method == "FIXED_CAPITAL":
            # Allocation / Price
            allocation = capital * 0.25 
            raw_qty = allocation / entry_price
            
            # Apply Lot Size
            qty = math.floor(raw_qty / lot_size) * lot_size

        elif self.config.method == "MARTINGALE":
            # Start with 1% risk, double for every consecutive loss
            base_risk_pct = self.config.risk_per_trade_pct
            multiplier = 2 ** consecutive_losses # 1, 2, 4, 8...
            
            # Cap multiplier to prevent blowing up account (e.g., max 4x)
            multiplier = min(multiplier, 4) 
            
            risk_amount = capital * base_risk_pct * multiplier
            risk_per_share = abs(entry_price - sl_price)
            raw_qty = risk_amount / risk_per_share
            
            qty = math.floor(raw_qty / lot_size) * lot_size

        elif self.config.method == "ANTI_MARTINGALE":
            # Increase risk slightly on winning streaks
            base_risk_pct = self.config.risk_per_trade_pct
            # Increase risk by 0.5% for every win, cap at 3%
            bonus_risk = min(consecutive_wins * 0.005, 0.03) 
            
            risk_amount = capital * (base_risk_pct + bonus_risk)
            risk_per_share = abs(entry_price - sl_price)
            raw_qty = risk_amount / risk_per_share
            
            qty = math.floor(raw_qty / lot_size) * lot_size

        return int(qty)