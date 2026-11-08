import pytest

from app.control.commands import CommandError, parse_order, parse_protection
from app.domain.models import ProductType, Side


def test_market_order():
    o = parse_order(Side.BUY, ["reliance", "10"])
    assert (o.symbol, o.qty, o.limit_price, o.product) == ("RELIANCE", 10, None, ProductType.MIS)


def test_limit_with_protection_and_cnc():
    o = parse_order(Side.BUY, ["INFY", "5", "@1500.5", "sl=1480", "tgt=1550", "cnc"])
    assert (o.limit_price, o.stop, o.target, o.product) == (1500.5, 1480, 1550, ProductType.CNC)
    assert parse_order(Side.SELL, ["SBIN", "1", "800"]).limit_price == 800


@pytest.mark.parametrize(
    "side,args,match",
    [
        (Side.BUY, ["X"], "Usage"),
        (Side.BUY, ["X", "ten"], "whole number"),
        (Side.BUY, ["X", "0"], "positive"),
        (Side.BUY, ["X", "1", "@100", "sl=101"], "below"),
        (Side.SELL, ["X", "1", "@100", "sl=99"], "above"),
        (Side.BUY, ["X", "1", "@100", "tgt=99"], "above"),
        (Side.BUY, ["X", "1", "sl=110", "tgt=100"], "wrong sides"),
        (Side.BUY, ["X", "1", "foo"], "Unrecognised"),
        (Side.BUY, ["X", "1", "sl=-5"], "positive"),
    ],
)
def test_invalid_orders(side, args, match):
    with pytest.raises(CommandError, match=match):
        parse_order(side, args)


def test_protection_partial_updates():
    assert parse_protection(["tcs", "sl=3900"]) == ("TCS", {"stop": 3900.0})
    assert parse_protection(["TCS", "tgt=0"]) == ("TCS", {"target": None})
    with pytest.raises(CommandError):
        parse_protection(["TCS"])
    with pytest.raises(CommandError):
        parse_protection(["TCS", "sl=abc"])
