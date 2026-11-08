"""Telegram control end-to-end on the paper broker and a real Postgres."""

import time
from datetime import datetime
from unittest.mock import patch

import pytest

from app.container import Container
from app.control.telegram_bot import TelegramBot
from app.core.clock import IST
from app.domain.models import Tick
from app.models import Instrument
from tests.fakes import FakeTelegram, RecordingNotifier
from tests.integration.test_app_startup import _test_settings

CHAT = "4242"
MARKET_TIME = datetime(2026, 11, 9, 10, 0, tzinfo=IST)


@pytest.fixture(autouse=True)
def market_hours():
    with patch("app.risk.manager.now_ist", return_value=MARKET_TIME):
        yield


async def _setup(sf):
    async with sf() as db, db.begin():
        db.add(Instrument(token=2885, trading_symbol="RELIANCE-EQ", segment="nse_cm", lot_size=1, tick_size=0.05))
    c = Container(_test_settings())
    c.notifier = RecordingNotifier()
    c.engine._notifier = c.notifier
    await c.instruments.load()
    await c.positions.load()
    c.bus.publish(Tick("2885", 100.0, MARKET_TIME, 1000))
    tg = FakeTelegram()
    bot = TelegramBot(tg, CHAT, c)
    return c, bot, tg


def msg(text: str, chat: str = CHAT) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": int(chat)}, "text": text}}


def tap(tg: FakeTelegram, ok: bool = True) -> dict:
    mid, _, markup = tg.sent[-1]
    data = markup["inline_keyboard"][0][0 if ok else 1]["callback_data"]
    return {
        "update_id": 2,
        "callback_query": {"id": "cb", "data": data, "message": {"message_id": mid, "chat": {"id": int(CHAT)}}},
    }


async def test_unauthorised_chat_is_ignored(sf):
    c, bot, tg = await _setup(sf)
    await bot.handle_update(msg("/kill", chat="999"))
    assert tg.sent == [] and not c.risk.kill_switch
    await c.db_engine.dispose()


async def test_manual_buy_with_stop_fill_alert_positions_and_exit(sf):
    c, bot, tg = await _setup(sf)

    await bot.handle_update(msg("/buy reliance 10 sl=95 tgt=110"))
    assert "Confirm?" in tg.last() and "BUY 10 RELIANCE-EQ MARKET" in tg.last()
    assert c.broker.is_paper and not c.oms.open_orders()  # nothing sent before confirmation

    await bot.handle_update(tap(tg))
    await c.oms.drain()
    edit = list(tg.edits.values())[-1]
    assert "Sent" in edit
    runner = c.engine.runner_for_symbol("RELIANCE")
    assert runner.cfg.strategy_type == "MANUAL"
    assert runner.position.qty == 10 and runner.state["stop"] == 95.0 and runner.state["target"] == 110.0
    assert any("BUY 10 RELIANCE-EQ" in m for lvl, m in c.notifier.messages if lvl == "HTML")  # fill alert

    await bot.handle_update(msg("/positions"))
    assert "RELIANCE-EQ" in tg.last() and "+10" in tg.last()

    await bot.handle_update(msg("/sl RELIANCE sl=97"))
    assert runner.state["stop"] == 97.0 and runner.state["target"] == 110.0

    await bot.handle_update(msg("/exit RELIANCE"))
    await bot.handle_update(tap(tg))
    await c.oms.drain()
    assert runner.position.qty == 0
    assert "closed" in list(tg.edits.values())[-1]
    await c.engine.shutdown()
    await c.oms.close()
    await c.db_engine.dispose()


async def test_cancel_button_and_expiry(sf):
    c, bot, tg = await _setup(sf)
    await bot.handle_update(msg("/buy RELIANCE 5"))
    await bot.handle_update(tap(tg, ok=False))
    assert "Cancelled" in list(tg.edits.values())[-1]
    assert c.positions.open_positions() == {}

    await bot.handle_update(msg("/kill testing"))
    for p in bot._pending.values():
        p.expires_at = time.monotonic() - 1
    await bot.handle_update(tap(tg))
    assert "Expired" in list(tg.edits.values())[-1] and not c.risk.kill_switch
    await c.db_engine.dispose()


async def test_errors_are_reported_not_raised(sf):
    c, bot, tg = await _setup(sf)
    await bot.handle_update(msg("/buy NOSUCH 1"))
    assert "Unknown symbol" in tg.last()
    await bot.handle_update(msg("/exit RELIANCE"))
    assert "No open position" in tg.last()
    await bot.handle_update(msg("/status"))
    assert "Day P&amp;L" in tg.last()
    await c.db_engine.dispose()


async def test_backlog_is_discarded_on_start(sf):
    c, bot, tg = await _setup(sf)
    tg.backlog = [{"update_id": 41, "message": {"chat": {"id": int(CHAT)}, "text": "/kill"}}]
    await bot._skip_backlog()
    assert bot._offset == 42 and not c.risk.kill_switch
    await c.db_engine.dispose()
