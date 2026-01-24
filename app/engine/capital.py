import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

class CapitalManager:
    """
    Manages position sizing based on the 'Slot Strategy'.
    Example: 10k Capital / 2 Slots = 5k Cash per trade.
    Buying Power = 5k * 5x Leverage = 25k per trade.
    """
    def __init__(self):
        self.total_capital = settings.MAX_CAPITAL_ALLOCATION
        self.max_slots = settings.MAX_CONCURRENT_TRADES
        self.leverage = settings.LEVERAGE_MULTIPLIER
        
        # Calculate power per slot once
        self.cash_per_slot = self.total_capital / self.max_slots
        self.power_per_slot = self.cash_per_slot * self.leverage
        
        logger.info(f"Capital Manager Initialized: {self.max_slots} Slots @ ₹{self.power_per_slot:,.2f} Power each.")

    def calculate_entry_quantity(self, current_price: float, active_trades_count: int) -> int:
        """
        Returns the number of shares to buy.
        Returns 0 if no slots are available.
        """
        # 1. Check Availability
        if active_trades_count >= self.max_slots:
            logger.warning(f"REJECTED: Max slots ({self.max_slots}) reached.")
            return 0

        if current_price <= 0:
            return 0

        # 2. Calculate Quantity (The "Two-Bullet" Math)
        # Quantity = (5000 * 5) / StockPrice
        quantity = int(self.power_per_slot / current_price)
        
        # 3. Safety Check: Minimum 1 qty
        if quantity < 1:
            logger.warning(f"REJECTED: Price {current_price} too high for slot size {self.power_per_slot}")
            return 0

        logger.info(f"APPROVED: Buy {quantity} qty of ₹{current_price} (Used Slot {active_trades_count + 1}/{self.max_slots})")
        return quantity