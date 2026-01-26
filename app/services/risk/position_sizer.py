import logging
import math

logger = logging.getLogger("RiskManager")


class CapitalManager:
    def __init__(self, total_capital: float, risk_per_trade_pct: float = 0.01):
        self.total_capital = total_capital
        self.risk_per_trade_pct = risk_per_trade_pct

    def calculate_quantity(self, entry_price: float, stop_loss: float) -> int:
        """
        Calculates qty based on Risk Amount.
        Risk Amount = Capital * 1%
        Qty = Risk Amount / (Entry - SL)
        """
        if entry_price <= 0 or stop_loss <= 0:
            logger.error("Invalid Entry/SL for sizing.")
            return 0

        risk_amount = self.total_capital * self.risk_per_trade_pct
        risk_per_share = abs(entry_price - stop_loss)

        qty = math.floor(risk_amount / risk_per_share)

        # Max Cap Check (e.g., Don't use more than 50% capital on one trade)
        max_capital_qty = math.floor((self.total_capital * 0.50) / entry_price)

        final_qty = min(qty, max_capital_qty)

        # Sanity Check
        if final_qty < 1:
            return 0

        return int(final_qty)
