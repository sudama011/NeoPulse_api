from datetime import datetime, timedelta

import pytest

from app.core.clock import IST, candle_start
from app.domain.models import PositionState, ProductType, Side, Tick
from app.market.candles import CandleBuilder
from app.portfolio.charges import ChargesCalculator
from app.risk.sizer import position_size
from app.strategy.base import protective_exit

T0 = datetime(2026, 11, 9, 9, 15, tzinfo=IST)


# ---------------------------------------------------------------------- positions
def test_position_open_add_reduce_close():
    p = PositionState()
    assert p.apply(Side.BUY, 10, 100) == 0
    assert p.apply(Side.BUY, 10, 110) == 0
    assert (p.qty, p.avg_price) == (20, 105)
    assert p.apply(Side.SELL, 5, 115) == pytest.approx(50)  # 5 * (115 - 105)
    assert (p.qty, p.avg_price) == (15, 105)
    assert p.apply(Side.SELL, 15, 100) == pytest.approx(-75)
    assert (p.qty, p.avg_price) == (0, 0.0)
    assert p.realized_pnl == pytest.approx(-25)


def test_position_short_and_flip_through_zero():
    p = PositionState()
    p.apply(Side.SELL, 10, 200)
    assert p.qty == -10
    realized = p.apply(Side.BUY, 15, 190)  # cover 10 (+100), open long 5 @ 190
    assert realized == pytest.approx(100)
    assert (p.qty, p.avg_price) == (5, 190)
    assert p.unrealized(200) == pytest.approx(50)


# ---------------------------------------------------------------------- charges
def test_intraday_charges_zero_brokerage():
    calc = ChargesCalculator()
    buy = calc.compute(Side.BUY, 100, 1000, ProductType.MIS)  # turnover 1,00,000
    sell = calc.compute(Side.SELL, 100, 1000, ProductType.MIS)
    # buy: exch 2.97 + sebi 0.1 + gst 0.5526 + stamp 3 ; sell adds STT 25 and no stamp
    assert buy == pytest.approx(6.6226, abs=1e-3)
    assert sell == pytest.approx(28.6226, abs=1e-3)


def test_delivery_charges_include_stt_both_sides():
    calc = ChargesCalculator()
    assert calc.compute(Side.BUY, 100, 1000, ProductType.CNC) > 100  # STT 100 + stamp 15
    assert calc.compute(Side.SELL, 100, 1000, ProductType.CNC) > 100


# ---------------------------------------------------------------------- sizing
def test_size_is_min_of_risk_and_capital():
    # risk: 1% of 1L = 1000 / (100-98)=2 -> 500 ; capital: 1L/100 = 1000
    assert position_size(capital=100_000, entry=100, stop=98) == 500
    # wide stop -> risk-bound ; tight stop -> capital-bound
    assert position_size(capital=100_000, entry=100, stop=99.99) == 1000
    assert position_size(capital=100_000, entry=100, stop=None, leverage=2) == 2000


def test_size_respects_confidence_and_lots():
    assert position_size(capital=100_000, entry=100, stop=98, confidence=0.5) == 250
    assert position_size(capital=100_000, entry=100, stop=98, lot_size=75) == 450
    assert position_size(capital=0, entry=100, stop=98) == 0


# ---------------------------------------------------------------------- candles
def _tick(minute: float, price: float, cum_vol: int) -> Tick:
    return Tick("1", price, T0 + timedelta(minutes=minute), cum_vol)


def test_candle_start_aligns_to_session_open():
    assert candle_start(T0 + timedelta(minutes=7, seconds=30), 5) == T0 + timedelta(minutes=5)
    assert candle_start(T0 + timedelta(minutes=16), 15) == T0 + timedelta(minutes=15)


def test_candle_builder_emits_on_bucket_change_with_volume_delta():
    b = CandleBuilder(5)
    assert b.update(_tick(0.1, 100, 1000)) is None
    assert b.update(_tick(1, 102, 1500)) is None
    assert b.update(_tick(4, 99, 1700)) is None
    closed = b.update(_tick(5.1, 101, 2000))
    assert closed is not None
    assert (closed.open, closed.high, closed.low, closed.close) == (100, 102, 99, 99)
    assert closed.volume == 700  # first tick has no reference -> deltas 500 + 200
    assert closed.start == T0


def test_candle_builder_flush_and_seed_prevent_duplicates():
    b = CandleBuilder(5, close_grace_seconds=1)
    b.update(_tick(0.5, 100, 10))
    assert b.flush(T0 + timedelta(minutes=4)) is None
    assert b.flush(T0 + timedelta(minutes=5, seconds=2)).close == 100

    seeded = CandleBuilder(5)
    from app.domain.models import Candle

    seeded.seed(Candle(T0, 1, 1, 1, 1))
    assert seeded.update(_tick(2, 100, 10)) is None  # tick for the already-processed bucket
    assert seeded._current is None


# ---------------------------------------------------------------------- protection
@pytest.mark.parametrize(
    "pos,stop,target,high,low,open_,expected",
    [
        (10, 95, 110, 101, 94, None, ("STOP_LOSS", 95)),
        (10, 95, 110, 111, 96, None, ("TARGET", 110)),
        (10, 95, 110, 111, 94, None, ("STOP_LOSS", 95)),  # both hit: stop wins
        (10, 95, 110, 93, 90, 92, ("STOP_LOSS", 92)),  # gap below stop fills at open
        (-10, 105, 90, 106, 100, None, ("STOP_LOSS", 105)),
        (-10, 105, 90, 100, 89, None, ("TARGET", 90)),
        (0, 95, 110, 200, 1, None, None),
        (10, None, None, 200, 1, None, None),
    ],
)
def test_protective_exit(pos, stop, target, high, low, open_, expected):
    assert protective_exit(pos, stop, target, high, low, open_) == expected
