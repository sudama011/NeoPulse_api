import logging
import math

logger = logging.getLogger("PositionSizer")


class PositionSizer:
    def __init__(self):
        pass

    def calculate_qty(
        self,
        total_capital: float,
        available_capital: float,
        max_slots: int,
        open_slots: int,
        entry_price: float,
        sl_price: float,
        lot_size: int = 1,
        confidence: float = 1.0,  # 0.5 to 2.0 multiplier
        risk_per_trade_pct: float = 0.01,
        leverage: float = 1.0,
    ) -> int:
        """
        Calculates quantity based on Risk, Slot Partitioning, and Confidence.
        """
        # Safety Guards
        if entry_price <= 0 or sl_price <= 0:
            logger.error("❌ Sizer: Invalid prices provided.")
            return 0

        if max_slots <= 0:
            return 0

        # 1. Determine Max Allocation per Slot (The "Fair Share")
        # Example: 100k capital / 4 slots = 25k per trade base.
        slot_allocation = total_capital / max_slots

        # 2. Apply Confidence Multiplier
        adjusted_allocation = slot_allocation * confidence

        # Guardrail: Never use more than what's actually available
        # Also, never hog 100% of available capital if other slots are waiting
        if open_slots > 1:
            # If we have spare slots, we can be aggressive, but cap at available
            max_allowed_cap = min(adjusted_allocation, available_capital)
        else:
            # Last slot? Strict limit to whatever is left or the slot size
            max_allowed_cap = min(slot_allocation, available_capital)

        # 3. Calculate Quantity based on Capital Limit
        qty_by_cap = (max_allowed_cap * leverage) / entry_price

        # 4. Calculate Quantity based on Risk Limit (Stop Loss)
        risk_amount = total_capital * risk_per_trade_pct
        risk_per_share = abs(entry_price - sl_price)

        if risk_per_share <= 0.05:  # Prevent huge size on tight SL
            # Fallback: Assume at least 0.5% risk if SL is too tight to be real
            risk_per_share = entry_price * 0.005

        qty_by_risk = risk_amount / risk_per_share

        # 5. Final Quantity is the Minimum of both checks
        raw_qty = min(qty_by_cap, qty_by_risk)

        # 6. Lot Size Adjustment
        qty = math.floor(raw_qty / lot_size) * lot_size

        # 7. Final Sanity Check
        if qty <= 0:
            return 0

        logger.info(
            f"🧮 Sizing: Cap(₹{max_allowed_cap:.0f}) vs Risk(₹{risk_amount:.0f}) " f"-> Qty {qty} (Conf: {confidence})"
        )

        return int(qty)
