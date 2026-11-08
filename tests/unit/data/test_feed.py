from unittest.mock import AsyncMock, MagicMock, patch

from neo_api_client.websocket.feed import SFeedMarketStatus, SFeedScrip

from app.data.feed import MarketFeed, normalize_message


def _scrip(**overrides):
    fields = dict(
        exchange_segment="nse_cm",
        instrument_token="2885",
        trading_symbol="RELIANCE-EQ",
        level=4,
        last_traded_price=2500.5,
        open_price=2490.0,
        high_price=2510.0,
        low_price=2480.0,
        close_price=2495.0,
        average_trade_price=2499.0,
        last_trade_time=1700000000,
        last_update_time=1700000000,
        last_trade_qty=10,
        total_buy_quantity=0,
        total_sell_quantity=0,
        volume_traded_today=123456,
        open_interest=0,
        net_change=5.5,
        net_change_percent=0.22,
        upper_circuit_limit=0.0,
        lower_circuit_limit=0.0,
        yearly_high=0.0,
        yearly_low=0.0,
        total_traded_value=0.0,
        market_lot=1,
        precision=2,
        multiplier=1,
    )
    fields.update(overrides)
    return SFeedScrip(**fields)


def test_normalize_scrip_to_legacy_tick():
    tick = normalize_message(_scrip())
    assert tick["tk"] == "2885"
    assert tick["ltp"] == 2500.5
    assert tick["v"] == 123456
    assert tick["ts"] == "RELIANCE-EQ"
    assert (tick["op"], tick["h"], tick["lo"], tick["c"]) == (2490.0, 2510.0, 2480.0, 2495.0)


def test_normalize_ignores_non_instrument_messages():
    msg = SFeedMarketStatus(exchange_segment="nse_cm", status_code=1, status="Market open")
    assert normalize_message(msg) is None


async def test_subscribe_queues_when_disconnected():
    feed = MarketFeed()
    await feed.subscribe([2885, "11536"])
    assert feed.subscribed_tokens == {"2885", "11536"}


async def test_subscribe_sends_when_connected():
    feed = MarketFeed()
    ws = MagicMock()
    ws.is_connected = True
    ws.subscribe_scrips = AsyncMock()
    feed._ws = ws

    await feed.subscribe(["2885"])

    sent = ws.subscribe_scrips.await_args.args[0]
    assert len(sent) == 1
    assert sent[0].exchange_segment == "nse_cm" and sent[0].instrument_token == "2885"


async def test_read_loop_publishes_normalized_ticks():
    feed = MarketFeed()

    class FakeWs:
        def __aiter__(self):
            async def gen():
                yield _scrip()
                yield SFeedMarketStatus(exchange_segment="nse_cm", status_code=1, status="Market open")

            return gen()

    with patch("app.data.feed.data_stream") as stream:
        stream.publish = AsyncMock()
        await feed._read_loop(FakeWs())

    stream.publish.assert_awaited_once()
    assert stream.publish.await_args.args[0][0]["tk"] == "2885"
