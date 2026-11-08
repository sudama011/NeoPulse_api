from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from neo_api_client.exceptions import ApiValueError

from app.broker.base import BrokerUnavailable, OrderRejected
from app.broker.kotak import KotakBroker, map_status, parse_order, parse_position
from app.broker.kotak_session import is_auth_error, response_error
from app.broker.paper import PaperBroker
from app.domain.models import OrderRequest, OrderStatus, OrderType, Side


def _req(**kw) -> OrderRequest:
    base = dict(client_id="NPTEST", symbol="RELIANCE-EQ", token="2885", side=Side.BUY, qty=10)
    base.update(kw)
    return OrderRequest(**base)


# ---------------------------------------------------------------------- kotak parsing
def test_map_status():
    assert map_status("complete") is OrderStatus.FILLED
    assert map_status("rejected") is OrderStatus.REJECTED
    assert map_status("cancelled") is OrderStatus.CANCELLED
    for raw in ("open", "open pending", "validation pending", "trigger pending", None, "something new"):
        assert map_status(raw) is OrderStatus.OPEN


def test_parse_order_row():
    o = parse_order(
        {
            "nOrdNo": "123",
            "ordSt": "complete",
            "fldQty": 10,
            "avgPrc": "2500.50",
            "qty": 10,
            "GuiOrdId": "NPABC",
            "tok": "2885",
            "rejRsn": "--",
        }
    )
    assert (o.broker_order_id, o.status, o.filled_qty, o.avg_price, o.client_id, o.message) == (
        "123",
        OrderStatus.FILLED,
        10,
        2500.5,
        "NPABC",
        None,
    )
    assert parse_order({"ordSt": "open"}) is None


def test_parse_position_net_and_average():
    p = parse_position(
        {
            "tok": "1",
            "trdSym": "X-EQ",
            "prod": "MIS",
            "flBuyQty": "10",
            "flSellQty": "4",
            "buyAmt": "1000.00",
            "sellAmt": "420.00",
        }
    )
    assert (p.net_qty, p.avg_price) == (6, 100.0)


def test_response_error_and_auth_detection():
    assert response_error({"stat": "Ok", "data": []}) is None
    assert response_error({"Error": "boom"}) == "boom"
    assert response_error({"stat": "Not_Ok", "errMsg": "RMS"}) == "RMS"
    assert is_auth_error("Complete the 2fa process before accessing this application")
    assert not is_auth_error("Insufficient funds")


# ---------------------------------------------------------------------- kotak place_order outcomes
def _kotak_with(resp):
    session = SimpleNamespace(
        client=SimpleNamespace(place_order=None), call=AsyncMock(return_value=resp), ensure_login=AsyncMock()
    )
    return KotakBroker(session)


async def test_kotak_place_order_success():
    assert await _kotak_with({"stat": "Ok", "nOrdNo": "999", "stCode": 200}).place_order(_req()) == "999"


async def test_kotak_validation_error_is_definitive_reject():
    with pytest.raises(OrderRejected):
        await _kotak_with({"Error": ApiValueError("bad price")}).place_order(_req())


async def test_kotak_transport_error_is_uncertain():
    with pytest.raises(BrokerUnavailable):
        await _kotak_with({"Error": ConnectionError("reset by peer")}).place_order(_req())


async def test_kotak_backend_reject():
    with pytest.raises(OrderRejected, match="margin"):
        await _kotak_with({"stat": "Not_Ok", "errMsg": "Insufficient margin"}).place_order(_req())


async def test_kotak_sends_tag_and_canonical_codes():
    broker = _kotak_with({"stat": "Ok", "nOrdNo": "1"})
    await broker.place_order(_req(order_type=OrderType.LIMIT, price=2500.0, side=Side.SELL))
    kwargs = broker.session.call.await_args.kwargs
    assert kwargs["tag"] == "NPTEST"
    assert kwargs["order_type"] == "L" and kwargs["price"] == "2500.00"
    assert kwargs["transaction_type"] == "S" and kwargs["product"] == "MIS"


# ---------------------------------------------------------------------- paper
async def test_paper_market_fill_and_limit_resting():
    prices = {"2885": 100.0}
    paper = PaperBroker(prices.get, slippage_bps=0)
    oid = await paper.place_order(_req())
    (o,) = await paper.order_book()
    assert o.broker_order_id == oid and o.status is OrderStatus.FILLED and o.avg_price == 100.0

    lim = await paper.place_order(_req(client_id="L1", order_type=OrderType.LIMIT, price=95.0))
    book = {o.broker_order_id: o for o in await paper.order_book()}
    assert book[lim].status is OrderStatus.OPEN
    prices["2885"] = 94.0
    book = {o.broker_order_id: o for o in await paper.order_book()}
    assert book[lim].status is OrderStatus.FILLED and book[lim].avg_price == 94.0

    (pos,) = await paper.positions()
    assert pos.net_qty == 20


async def test_paper_rejects_without_price():
    with pytest.raises(OrderRejected):
        await PaperBroker(lambda t: None).place_order(_req())
