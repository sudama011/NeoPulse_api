from unittest.mock import AsyncMock, MagicMock, patch

from app.execution.kotak import KotakNeoAdapter, _enrich_position, _sdk_error


def test_sdk_error_detection():
    assert _sdk_error({"stat": "Ok", "nOrdNo": "1"}) is None
    assert "Invalid order type" in _sdk_error({"Error": "Invalid order type"})
    assert _sdk_error({"error": [{"code": "400"}]})
    assert _sdk_error({"Error Message": "Complete the 2fa process"})
    assert _sdk_error({"stat": "Not_Ok", "errMsg": "RMS reject"}) == "RMS reject"


def test_enrich_open_long_position():
    pos = _enrich_position(
        {"tok": "2885", "trdSym": "RELIANCE-EQ", "flBuyQty": "10", "flSellQty": "0", "buyAmt": "25000.00"}
    )
    assert pos["netQty"] == 10
    assert pos["avgPrice"] == 2500.0
    assert pos["realizedPNL"] == 0.0
    assert pos["instrumentToken"] == "2885"
    assert pos["instrumentName"] == "RELIANCE-EQ"


def test_enrich_closed_position_realized_pnl():
    pos = _enrich_position(
        {"tok": "1", "flBuyQty": "10", "flSellQty": "10", "buyAmt": "1000.00", "sellAmt": "1050.00"}
    )
    assert pos["netQty"] == 0
    assert pos["avgPrice"] == 0.0
    assert pos["realizedPNL"] == 50.0


def _adapter():
    with patch("app.execution.kotak.NeoAPI") as neo:
        adapter = KotakNeoAdapter()
    adapter.client = neo.return_value
    return adapter


async def test_place_order_maps_sdk_error_to_errmsg():
    adapter = _adapter()
    with patch("app.execution.kotak.run_blocking", AsyncMock(return_value={"Error": "bad price"})), patch(
        "app.execution.kotak.notification_manager"
    ) as notifier:
        notifier.notify_trade = AsyncMock()
        resp = await adapter.place_order(
            {"trading_symbol": "X-EQ", "transaction_type": "B", "quantity": 1, "price": 0, "order_type": "L"}
        )
    assert resp["stat"] == "Not_Ok"
    assert resp["errMsg"] == "bad price"
    notifier.notify_trade.assert_not_awaited()


async def test_limits_exposes_lowercase_net():
    adapter = _adapter()
    with patch("app.execution.kotak.run_blocking", AsyncMock(return_value={"stat": "Ok", "Net": "1500.5"})):
        resp = await adapter.get_limits()
    assert resp["net"] == "1500.5"


def test_login_raises_on_error_payload():
    adapter = _adapter()
    adapter.client.totp_login = MagicMock(return_value={"error": [{"code": "400", "message": "bad totp"}]})
    with patch("app.execution.kotak.pyotp"):
        try:
            adapter._login_sync()
            raised = False
        except ConnectionError:
            raised = True
    assert raised
