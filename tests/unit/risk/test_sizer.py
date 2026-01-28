import pytest
from app.risk.sizer import PositionSizer

@pytest.fixture
def sizer():
    return PositionSizer()

def test_sizer_basic_calculation(sizer):
    """Test standard sizing based on risk percentage."""
    qty = sizer.calculate_qty(
        total_capital=100_000,
        available_capital=100_000,
        max_slots=4,
        open_slots=4,
        entry_price=1000,
        sl_price=990,  # 10 Rs Risk per share
        risk_per_trade_pct=0.01  # 1% Risk = 1000 Rs
    )
    # Risk Amount = 1000. Risk Per Share = 10. Qty = 100.
    # Capital required = 100 * 1000 = 1L. Allowed per slot = 25k.
    # So it should be capped by Slot Allocation (25k / 1000 = 25 qty).
    assert qty == 25

def test_sizer_risk_limit(sizer):
    """Test where Risk Limit is the bottleneck, not Capital."""
    qty = sizer.calculate_qty(
        total_capital=100_000,
        available_capital=100_000,
        max_slots=1,  # Full capital allowed (100k)
        open_slots=1,
        entry_price=100,
        sl_price=90,  # 10 Rs Risk
        risk_per_trade_pct=0.01  # Risk = 1000 Rs
    )
    # Qty by Risk = 1000 / 10 = 100.
    # Qty by Cap = 100,000 / 100 = 1000.
    # Should pick smaller (100).
    assert qty == 100

def test_sizer_zero_division_protection(sizer):
    """Test that entry == sl does not crash the bot."""
    qty = sizer.calculate_qty(
        total_capital=100_000,
        available_capital=100_000,
        max_slots=1,
        open_slots=1,
        entry_price=100,
        sl_price=100,  # Invalid SL
    )
    # Logic should use fallback risk (0.5%) -> 100 * 0.005 = 0.5 Rs risk
    # Risk Amount = 1000. Qty = 2000.
    # Cap limit = 1000. Final = 1000.
    assert qty > 0

def test_sizer_lot_size_adjustment(sizer):
    """Test rounding down to lot size."""
    qty = sizer.calculate_qty(
        total_capital=100_000,
        available_capital=100_000,
        max_slots=1,
        open_slots=1,
        entry_price=100,
        sl_price=95,
        lot_size=75,
        risk_per_trade_pct=0.10 # High risk to allow large size
    )
    assert qty % 75 == 0