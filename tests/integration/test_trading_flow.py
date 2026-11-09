"""
End-to-end flows on a real Postgres with a scripted broker.
Covers write-ahead orders, partial fills, uncertain sends, crash recovery, risk gating,
stop-loss, kill switch, auto-disable and reconciliation.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.core.clock import IST
from app.core.settings import Settings
from app.domain.models import BrokerPosition, Candle, OrderStatus, ProductType, Side, Tick
from app.market.bus import TickBus
from app.market.instruments import InstrumentStore
from app.models import FillRecord, Instrument, Order, StrategyInstance
from app.oms.order_manager import OrderManager
from app.oms.reconciler import PositionReconciler
from app.portfolio.book import PositionBook
from app.portfolio.charges import ChargesCalculator
from app.risk.manager import RiskManager
from app.strategy import register_strategy
from app.strategy.base import Strategy
from app.strategy.engine import NewInstance, StrategyEngine
from tests.fakes import BrokerUnavailable, FakeBroker, OrderRejected, RecordingNotifier

MARKET_TIME = datetime(2026, 11, 9, 10, 0, tzinfo=IST)  # a Monday, inside the entry window


# ---------------------------------------------------------------------- test strategy
@register_strategy("TEST_SCRIPTED")
class ScriptedStrategy(Strategy):
    """Test strategy driven by params: buys on first tick, optional stop, optional crash."""

    warmup = 0

    async def on_candle(self, candle: Candle) -> None:
        pass

    async def on_tick(self, tick: Tick) -> None:
        if self.params.get("crash"):
            raise RuntimeError("boom")
        if self.position == 0 and not self.ctx.has_pending_order and not self.state.get("done"):
            if await self.buy(
                tick.ltp, qty=self.params.get("qty", 10), stop=self.params.get("stop"), reason="TEST_ENTRY"
            ):
                self.state["done"] = True


class FakeFeed:
    def __init__(self):
        self.tokens: set[str] = set()

    async def subscribe(self, tokens):
        self.tokens |= set(tokens)

    async def unsubscribe(self, tokens):
        self.tokens -= set(tokens)


class FakeHistory:
    def __init__(self, candles=None, fail_times: int = 0):
        self.data = candles or []
        self.fail_times = fail_times
        self.calls = 0

    async def candles(self, token, timeframe, count, segment="nse_cm", retries=0):
        from app.market.history import HistoryUnavailable

        self.calls += 1
        if self.calls <= self.fail_times:
            raise HistoryUnavailable("Rate limit exceeded")
        return [c for c in self.data][-count:]


@dataclass
class System:
    sf: object
    broker: FakeBroker
    bus: TickBus
    positions: PositionBook
    oms: OrderManager
    risk: RiskManager
    engine: StrategyEngine
    notifier: RecordingNotifier


async def build(sf, broker=None, **settings_overrides) -> System:
    async with sf() as db, db.begin():
        db.add(Instrument(token=2885, trading_symbol="RELIANCE-EQ", segment="nse_cm", lot_size=1, tick_size=0.05))
    broker = broker or FakeBroker()
    settings = Settings(**{"MAX_DAILY_LOSS": 1000.0, "MAX_OPEN_POSITIONS": 3, **settings_overrides})
    notifier = RecordingNotifier()
    bus = TickBus()
    positions = PositionBook(sf, ChargesCalculator())
    await positions.load()
    oms = OrderManager(broker, sf, positions, limit_order_ttl=60)
    risk = RiskManager(settings, positions, broker, bus.last_price, sf, notifier)
    instruments = InstrumentStore(sf)
    await instruments.load()
    engine = StrategyEngine(sf, oms, positions, risk, bus, FakeHistory(), instruments, FakeFeed(), notifier)
    risk.on_kill(engine.flatten_all)
    return System(sf, broker, bus, positions, oms, risk, engine, notifier)


def tick(price: float) -> Tick:
    return Tick("2885", price, MARKET_TIME, 1000)


@pytest.fixture(autouse=True)
def market_hours():
    with patch("app.risk.manager.now_ist", return_value=MARKET_TIME):
        yield


async def submit(s: System, side=Side.BUY, qty=10, instance_id=None):
    return await s.oms.submit(
        instance_id=instance_id,
        symbol="RELIANCE-EQ",
        token="2885",
        side=side,
        qty=qty,
        product=ProductType.MIS,
        reason="test",
    )


async def make_instance(s: System, **params) -> str:
    row = await s.engine.create(
        NewInstance(strategy_type="TEST_SCRIPTED", symbol="RELIANCE", capital=100_000, params=params)
    )
    return row.id


async def drive(s: System, price: float) -> None:
    """Publishes a tick and lets runners process it."""
    s.bus.publish(tick(price))
    for _ in range(5):
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------- OMS
async def test_write_ahead_partial_fills_and_idempotency(sf):
    s = await build(sf)
    iid = await make_instance(s)
    view = await submit(s, qty=10, instance_id=iid)
    assert view.status is OrderStatus.OPEN

    oid = s.broker.last_order_id()
    await s.oms.on_broker_update(s.broker.fill(oid, 4, 100.0))
    await s.oms.on_broker_update(s.broker.fill(oid, 4, 100.0))  # duplicate (feed + poll)
    await s.oms.on_broker_update(s.broker.fill(oid, 10, 101.2))  # avg 101.2 => 6 @ 102
    assert s.positions.get(iid).qty == 10

    async with sf() as db:
        fills = (await db.execute(select(FillRecord).order_by(FillRecord.id))).scalars().all()
        order = await db.get(Order, view.id)
        inst = await db.get(StrategyInstance, iid)
    assert [(f.qty, round(f.price, 2)) for f in fills] == [(4, 100.0), (6, 102.0)]
    assert (order.status, order.filled_qty, inst.position_qty) == ("FILLED", 10, 10)
    assert s.oms.open_orders() == []


async def test_definitive_reject_is_recorded(sf):
    s = await build(sf)
    iid = await make_instance(s)
    s.broker.fail_next = OrderRejected("insufficient margin")
    view = await submit(s, instance_id=iid)
    assert view.status is OrderStatus.REJECTED
    assert s.oms.open_orders() == []


async def test_lost_response_is_resolved_by_tag_without_resending(sf):
    s = await build(sf)
    iid = await make_instance(s)
    s.broker.place_but_raise = True
    view = await submit(s, instance_id=iid)
    assert view.status is OrderStatus.UNKNOWN and len(s.broker.requests) == 1

    s.broker.fill(s.broker.last_order_id(), 10, 100.0)
    await s.oms.sync_with_broker()
    assert view.status is OrderStatus.FILLED
    assert s.positions.get(iid).qty == 10
    assert len(s.broker.requests) == 1  # never resent


async def test_unknown_order_absent_from_book_becomes_failed(sf):
    s = await build(sf)
    iid = await make_instance(s)
    s.broker.fail_next = BrokerUnavailable("timeout before send")
    view = await submit(s, instance_id=iid)
    view.created_at -= timedelta(minutes=5)
    await s.oms.sync_with_broker()
    assert view.status is OrderStatus.FAILED


async def test_crash_recovery_applies_fills_once(sf):
    s = await build(sf)
    iid = await make_instance(s)
    await submit(s, qty=10, instance_id=iid)
    oid = s.broker.last_order_id()
    await s.engine.shutdown()

    # --- process "crashes"; the order fills while we are down ---
    s.broker.fill(oid, 10, 100.0)
    s2 = await build_restart(sf, s.broker)
    await s2.oms.recover()
    await s2.oms.recover()  # running recovery twice must not double count
    assert s2.positions.get(iid).qty == 10
    async with sf() as db:
        assert (await db.execute(select(func.count()).select_from(FillRecord))).scalar() == 1


async def build_restart(sf, broker) -> System:
    settings = Settings(MAX_DAILY_LOSS=1000.0)
    notifier = RecordingNotifier()
    bus = TickBus()
    positions = PositionBook(sf, ChargesCalculator())
    await positions.load()
    oms = OrderManager(broker, sf, positions)
    risk = RiskManager(settings, positions, broker, bus.last_price, sf, notifier)
    instruments = InstrumentStore(sf)
    await instruments.load()
    engine = StrategyEngine(sf, oms, positions, risk, bus, FakeHistory(), instruments, FakeFeed(), notifier)
    return System(sf, broker, bus, positions, oms, risk, engine, notifier)


async def test_previous_day_open_orders_expire_on_recovery(sf):
    s = await build(sf)
    iid = await make_instance(s)
    view = await submit(s, instance_id=iid)
    async with sf() as db, db.begin():
        row = await db.get(Order, view.id)
        row.created_at = row.created_at - timedelta(days=1)
    s2 = await build_restart(sf, s.broker)
    await s2.oms.recover()
    async with sf() as db:
        assert (await db.get(Order, view.id)).status == "CANCELLED"


# ---------------------------------------------------------------------- runner & engine
async def test_strategy_entry_pending_guard_and_stop_loss(sf):
    s = await build(sf)
    iid = await make_instance(s, qty=10, stop=95.0)
    runner = s.engine.runners[iid]

    await drive(s, 100.0)
    await drive(s, 100.5)  # order still working -> no duplicate entry
    assert len(s.broker.requests) == 1
    await s.oms.on_broker_update(s.broker.fill(s.broker.last_order_id(), 10, 100.0))
    await s.oms.drain()
    assert runner.position.qty == 10 and runner.state["stop"] == 95.0

    await drive(s, 94.9)  # stop hit -> market exit
    exit_req = s.broker.requests[-1]
    assert (exit_req.side, exit_req.qty) == (Side.SELL, 10)
    await s.oms.on_broker_update(s.broker.fill(s.broker.last_order_id(), 10, 94.9))
    await s.oms.drain()
    assert runner.position.qty == 0 and "stop" not in runner.state

    async with sf() as db:
        state = (await db.get(StrategyInstance, iid)).state
    assert state.get("done") is True  # strategy state persisted
    await s.engine.shutdown()


async def test_risk_blocks_entries_outside_window_but_never_exits(sf):
    s = await build(sf)
    iid = await make_instance(s)
    runner = s.engine.runners[iid]
    await s.positions.adopt(iid, 10, 100.0)

    with patch("app.risk.manager.now_ist", return_value=MARKET_TIME.replace(hour=15, minute=20)):
        assert not await runner.submit(Side.BUY, 5, reason="late entry")
        assert await runner.submit(Side.SELL, 10, reason="exit", is_exit=True)
    assert [r.side for r in s.broker.requests] == [Side.SELL]
    await s.engine.shutdown()


async def test_kill_switch_trips_on_daily_loss_and_flattens(sf):
    s = await build(sf)
    iid = await make_instance(s)
    await s.positions.adopt(iid, 100, 100.0)
    s.broker.auto_fill_price = 89.0
    s.bus.publish(tick(89.0))  # unrealized -1100 > max loss 1000
    await s.risk.monitor()
    for _ in range(50):
        if s.positions.get(iid).qty == 0:
            break
        await asyncio.sleep(0.05)
    assert s.risk.kill_switch
    assert s.positions.get(iid).qty == 0
    assert await s.risk.check_entry(iid, 1, 100.0) is not None

    s2 = await build_restart(sf, s.broker)
    await s2.risk.load()
    assert s2.risk.kill_switch  # persisted across restart
    await s.engine.shutdown()


async def test_strategy_auto_disabled_after_repeated_errors_and_flattened(sf):
    s = await build(sf)
    iid = await make_instance(s, crash=True)
    await s.positions.adopt(iid, 5, 100.0)
    s.broker.auto_fill_price = 100.0
    for _ in range(5):
        await drive(s, 100.0)
    for _ in range(50):
        if iid not in s.engine.runners:
            break
        await asyncio.sleep(0.05)
    async with sf() as db:
        row = await db.get(StrategyInstance, iid)
    assert row.status == "DISABLED"
    assert s.positions.get(iid).qty == 0
    assert any(level == "CRITICAL" for level, _ in s.notifier.messages)


async def test_square_off_closes_intraday_positions(sf):
    s = await build(sf)
    iid = await make_instance(s)
    await s.positions.adopt(iid, -7, 100.0)
    s.broker.auto_fill_price = 99.0
    await s.engine.square_off_intraday()
    assert s.positions.get(iid).qty == 0
    assert (s.broker.requests[-1].side, s.broker.requests[-1].qty) == (Side.BUY, 7)
    await s.engine.shutdown()


async def test_one_active_instance_per_symbol(sf):
    s = await build(sf)
    await make_instance(s)
    with pytest.raises(ValueError, match="already has an active strategy"):
        await make_instance(s)
    await s.engine.shutdown()


async def test_stop_then_resume_instance(sf):
    s = await build(sf)
    iid = await make_instance(s)
    assert await s.engine.stop(iid, flatten=True)
    assert iid not in s.engine.runners
    await s.engine.resume(iid)
    assert iid in s.engine.runners
    await s.engine.shutdown()


# ---------------------------------------------------------------------- reconciliation
async def test_reconciler_adopts_broker_position_after_confirmation(sf):
    s = await build(sf)
    iid = await make_instance(s)
    await s.positions.adopt(iid, 10, 100.0)
    s.broker.broker_positions = [BrokerPosition("2885", "RELIANCE-EQ", "MIS", 0, 0.0)]  # broker squared off
    rec = PositionReconciler(s.broker, s.positions, s.oms, s.notifier)

    await rec.reconcile()
    assert s.positions.get(iid).qty == 10  # first mismatch: wait for confirmation
    await rec.reconcile()
    assert s.positions.get(iid).qty == 0
    async with sf() as db:
        assert (await db.get(StrategyInstance, iid)).position_qty == 0

    s.broker.broker_positions.append(BrokerPosition("999", "OTHER-EQ", "MIS", 3, 50.0))
    await rec.reconcile()
    assert any("Untracked" in m for _, m in s.notifier.messages)
    await s.engine.shutdown()


# ---------------------------------------------------------------------- failure-mode regressions
async def test_concurrent_events_never_double_submit(sf):
    s = await build(sf)
    iid = await make_instance(s, qty=10)
    runner = s.engine.runners[iid]
    await asyncio.gather(*(runner.on_tick(tick(100.0)) for _ in range(5)))
    assert len(s.broker.requests) == 1
    await s.engine.shutdown()


async def test_rejections_trigger_cooldown_instead_of_order_storm(sf):
    s = await build(sf)
    iid = await make_instance(s)
    runner = s.engine.runners[iid]
    await s.positions.adopt(iid, 10, 100.0)
    runner.state["stop"] = 95.0
    for _ in range(5):
        s.broker.fail_next = OrderRejected("exchange down")
        await runner.on_tick(tick(90.0))
        await s.oms.drain()
    assert len(s.broker.requests) == 1  # later ticks are inside the cooldown window
    await s.engine.shutdown()


async def test_in_flight_order_is_not_failed_by_reconciliation(sf):
    s = await build(sf)
    iid = await make_instance(s)
    gate = asyncio.Event()
    original = s.broker.place_order

    async def slow_place(req):
        await gate.wait()
        return await original(req)

    s.broker.place_order = slow_place
    submit_task = asyncio.create_task(submit(s, instance_id=iid))
    await asyncio.sleep(0.05)
    (view,) = s.oms.open_orders(iid)
    view.created_at -= timedelta(minutes=5)
    await s.oms.sync_with_broker()
    assert view.status is OrderStatus.PENDING
    gate.set()
    assert (await submit_task).status is OrderStatus.OPEN
    await s.engine.shutdown()


async def test_live_order_missing_from_book_is_not_assumed_dead(sf):
    s = await build(sf)
    iid = await make_instance(s)
    view = await submit(s, instance_id=iid)
    s.broker.book.clear()
    view.last_seen -= 1000
    await s.oms.sync_with_broker()
    assert view.status is OrderStatus.OPEN and s.oms.open_orders(iid)
    await s.engine.shutdown()


async def test_square_off_includes_orphaned_positions(sf):
    s = await build(sf)
    iid = await make_instance(s)
    await s.engine.stop(iid, flatten=False)
    await s.positions.adopt(iid, 4, 100.0)
    s.broker.auto_fill_price = 101.0
    await s.engine.square_off_intraday()
    assert s.positions.get(iid).qty == 0


def test_settings_reject_entry_cutoff_after_square_off():
    with pytest.raises(ValueError, match="ENTRY_CUTOFF"):
        Settings(ENTRY_CUTOFF="15:20", SQUARE_OFF_TIME="15:15")


# ---------------------------------------------------------------------- warm-up resilience
@register_strategy("TEST_RECORDER")
class RecorderStrategy(Strategy):
    """Records candles it sees; tries to buy on every candle (must be suppressed during replay)."""

    warmup = 3

    async def on_candle(self, candle: Candle) -> None:
        self.state.setdefault("seen", []).append(candle.start.isoformat())
        if self.position == 0 and not self.ctx.has_pending_order:
            await self.buy(candle.close, qty=1, reason="REC")


def _bar(minute: int, price: float = 100.0) -> Candle:
    return Candle(MARKET_TIME.replace(hour=9, minute=15) + timedelta(minutes=minute), price, price, price, price, 10)


async def test_runner_runs_live_while_history_is_rate_limited_then_replays_in_order(sf):
    s = await build(sf)
    history = FakeHistory([_bar(-15 * i) for i in range(6, 0, -1)], fail_times=1)
    s.engine._deps.history = history
    row = await s.engine.create(
        NewInstance(strategy_type="TEST_RECORDER", symbol="RELIANCE", capital=100_000, params={}, timeframe=15)
    )
    runner = s.engine.runners[row.id]
    assert runner.running and not runner.warmed_up

    # A protective exit still works while warming up; a new entry does not.
    await s.positions.adopt(row.id, 5, 100.0)
    runner.state["stop"] = 95.0
    s.broker.auto_fill_price = 94.0
    await runner.on_tick(tick(94.0))
    assert [r.side for r in s.broker.requests] == [Side.SELL]
    await s.oms.sync_with_broker()
    assert await runner.submit(Side.BUY, 1, reason="early") is False

    # A live candle closes while warming: it is held back, not given to the strategy.
    await runner.on_timer(MARKET_TIME.replace(hour=10, minute=16))
    assert len(runner._held) == 1 and "seen" not in runner.state

    assert await runner._try_warm_up(retries=0)
    seen = runner.state["seen"]
    # 6 history bars (strategy sees them from bar #3 = warmup) + the held live bar, in time order
    assert seen == sorted(seen) and len(seen) == 6 - 2 + 1
    assert seen[-1] == MARKET_TIME.replace(hour=10, minute=0).isoformat() and runner._held == []
    assert len(s.broker.requests) == 1  # replay never sends orders
    await s.engine.shutdown()


async def test_first_partial_candle_after_restart_is_replaced_by_exchange_candle(sf):
    s = await build(sf)
    full = Candle(MARKET_TIME.replace(hour=9, minute=15), 100.0, 105.0, 99.0, 104.0, 5000)
    history = FakeHistory([_bar(-15 * i) for i in range(6, 0, -1)])
    s.engine._deps.history = history
    with patch("app.strategy.runner.now_ist", return_value=MARKET_TIME.replace(hour=9, minute=22)):
        row = await s.engine.create(
            NewInstance(strategy_type="TEST_RECORDER", symbol="RELIANCE", capital=100_000, params={}, timeframe=15)
        )
    runner = s.engine.runners[row.id]
    history.data.append(full)
    # ticks only from 09:22 onwards -> partial 09:15 candle
    await runner.on_tick(Tick("2885", 102.0, MARKET_TIME.replace(hour=9, minute=22), 100))
    await runner.on_tick(Tick("2885", 103.0, MARKET_TIME.replace(hour=9, minute=31), 200))
    last = runner.strategy.candles[-1]
    assert (last.start, last.high, last.low, last.volume) == (full.start, 105.0, 99.0, 5000)
    await s.engine.shutdown()
