"""
Live runner for one strategy instance. Implements `StrategyContext`.

Responsibilities:
- warm up indicators from historical candles (so a restart is immediately "hot")
- build candles from ticks, feed the strategy, close quiet candles on time
- guard order flow: one working order at a time, entries pass risk checks, exits never blocked
- enforce persisted stop-loss / target on every tick
- persist strategy state; isolate and count strategy errors (auto-disable + flatten)
"""

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from app.core.clock import now_ist
from app.core.tasks import fire_and_forget
from app.domain.models import Fill, OrderStatus, OrderType, PositionState, ProductType, Side, Tick
from app.market.bus import TickBus
from app.market.candles import CandleBuilder
from app.market.history import HistoryService
from app.market.instruments import InstrumentInfo
from app.oms.order_manager import OrderManager, OrderView
from app.portfolio.book import PositionBook
from app.risk.manager import RiskManager
from app.risk.sizer import position_size
from app.strategy.base import Strategy, protective_exit

MAX_CONSECUTIVE_ERRORS = 5


@dataclass(frozen=True)
class InstanceConfig:
    id: str
    name: str
    strategy_type: str
    symbol: str
    token: str
    segment: str
    product: ProductType
    timeframe: int
    params: dict[str, Any]
    capital: float
    risk_per_trade_pct: float
    leverage: float
    state: dict[str, Any]


@dataclass
class RunnerDeps:
    oms: OrderManager
    positions: PositionBook
    risk: RiskManager
    bus: TickBus
    history: HistoryService
    save_state: Callable[[str, dict], Awaitable[None]]
    on_disable: Callable[[str, str], Awaitable[None]]
    alert: Callable[[str], None] = lambda message: None


class StrategyRunner:
    def __init__(self, cfg: InstanceConfig, strategy_cls: type[Strategy], instrument: InstrumentInfo, deps: RunnerDeps):
        self.cfg = cfg
        self.instance_id = cfg.id
        self.symbol = cfg.symbol
        self.token = cfg.token
        self.timeframe = cfg.timeframe
        self.instrument = instrument
        self.state: dict[str, Any] = dict(cfg.state or {})
        self.logger = logging.getLogger(f"strategy.{cfg.name}")
        self._deps = deps
        self._builder = CandleBuilder(cfg.timeframe)
        self._warming = False
        self._accepting = False
        self._errors = 0
        self._rejects = 0
        self._cooldown_until = 0.0
        # Serialises every event (tick, candle timer, fill, flatten) for this instance, so the
        # strategy never runs concurrently with itself and cannot double-submit.
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._saved_state = json.dumps(self.state, sort_keys=True, default=str)
        self.strategy: Strategy = strategy_cls(self, cfg.params)
        self.last_tick: Optional[Tick] = None

    # ------------------------------------------------------------------ StrategyContext
    @property
    def position(self) -> PositionState:
        return self._deps.positions.get(self.instance_id)

    @property
    def has_pending_order(self) -> bool:
        return bool(self._deps.oms.open_orders(self.instance_id))

    @property
    def is_warming_up(self) -> bool:
        return self._warming

    def now(self) -> datetime:
        return now_ist()

    def size(self, entry: float, stop: Optional[float], confidence: float) -> int:
        return position_size(
            capital=self.cfg.capital,
            entry=entry,
            stop=stop,
            risk_per_trade_pct=self.cfg.risk_per_trade_pct,
            leverage=self.cfg.leverage,
            confidence=confidence,
            lot_size=self.instrument.lot_size,
        )

    async def submit(
        self,
        side: Side,
        qty: int,
        *,
        reason: str,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        is_exit: bool = False,
    ) -> bool:
        if self._warming or not self._accepting:
            return False
        if self.has_pending_order:
            self.logger.info("Signal %s skipped: an order is already working", reason)
            return False
        if time.monotonic() < self._cooldown_until:
            self.logger.warning("Signal %s skipped: cooling down after %d rejection(s)", reason, self._rejects)
            return False
        qty = self.instrument.round_qty(qty)
        if qty <= 0:
            return False

        pos = self.position.qty
        reduces = pos != 0 and (pos > 0) != (side is Side.BUY) and qty <= abs(pos)
        if not (is_exit or reduces):
            ref_price = price or (self.last_tick.ltp if self.last_tick else 0.0)
            rejection = await self._deps.risk.check_entry(self.instance_id, qty, ref_price, self.cfg.leverage)
            if rejection:
                self.logger.warning("Entry %s blocked by risk: %s", reason, rejection)
                return False

        if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            price = self.instrument.round_price(price)
        view = await self._deps.oms.submit(
            instance_id=self.instance_id,
            symbol=self.symbol,
            token=self.token,
            side=side,
            qty=qty,
            order_type=order_type,
            product=self.cfg.product,
            price=price,
            segment=self.cfg.segment,
            reason=reason,
        )
        return view.status not in (OrderStatus.REJECTED, OrderStatus.FAILED)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await self._warm_up()
        self._deps.oms.add_listener(self.instance_id, self)
        self._accepting = True
        self._task = asyncio.create_task(self._consume(), name=f"runner-{self.cfg.name}")
        self.logger.info(
            "▶️ Running (%s %s, %dm, pos=%d)", self.cfg.strategy_type, self.symbol, self.timeframe, self.position.qty
        )

    async def stop(self) -> None:
        self._accepting = False
        self._deps.oms.remove_listener(self.instance_id)
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._persist_state()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _warm_up(self) -> None:
        need = self.strategy.warmup + 5
        if self.strategy.warmup <= 0:
            return
        candles = await self._deps.history.candles(self.token, self.timeframe, need, self.cfg.segment)
        if len(candles) < self.strategy.warmup:
            self.logger.warning(
                "Only %d/%d warm-up candles available; strategy will finish warming live",
                len(candles),
                self.strategy.warmup,
            )
        self._warming = True
        try:
            for candle in candles:
                await self._guard(self.strategy.process_candle(candle), "warmup")
        finally:
            self._warming = False
        if candles:
            self._builder.seed(candles[-1])

    async def _consume(self) -> None:
        sub = self._deps.bus.subscribe(self.token)
        try:
            while True:
                await self.on_tick(await sub.get())
        finally:
            sub.close()

    # ------------------------------------------------------------------ events
    async def on_tick(self, tick: Tick) -> None:
        async with self._lock:
            self.last_tick = tick
            candle = self._builder.update(tick)
            if candle is not None:
                await self._guard(self.strategy.process_candle(candle), "on_candle")
            await self._enforce_protection(tick.ltp)
            await self._guard(self.strategy.on_tick(tick), "on_tick")
            await self._persist_state()

    async def on_timer(self, now: datetime) -> None:
        async with self._lock:
            candle = self._builder.flush(now)
            if candle is not None:
                await self._guard(self.strategy.process_candle(candle), "on_candle")
                await self._persist_state()

    async def on_fill(self, fill: Fill) -> None:
        async with self._lock:
            self._rejects, self._cooldown_until = 0, 0.0
            if self.position.qty == 0:
                self._clear_protection()
            await self._guard(self.strategy.on_fill(fill), "on_fill")
            await self._persist_state()

    async def on_order_update(self, order: OrderView) -> None:
        if order.status not in (OrderStatus.REJECTED, OrderStatus.FAILED, OrderStatus.CANCELLED):
            return
        async with self._lock:
            if order.status in (OrderStatus.REJECTED, OrderStatus.FAILED):
                self._rejects += 1
                backoff = min(2**self._rejects, 300)
                self._cooldown_until = time.monotonic() + backoff
                self.logger.error(
                    "Order %s %s (%s); pausing orders for %ss", order.id, order.status, order.message, backoff
                )
                if self._rejects == 3:
                    self._deps.alert(f"{self.cfg.name}: 3 consecutive order rejections. Last: {order.message}")
            if self.position.qty == 0 and not self.has_pending_order:
                self._clear_protection()
            await self._persist_state()

    def _clear_protection(self) -> None:
        self.state.pop("stop", None)
        self.state.pop("target", None)

    # ------------------------------------------------------------------ protection & flatten
    async def _enforce_protection(self, ltp: float) -> None:
        hit = protective_exit(self.position.qty, self.state.get("stop"), self.state.get("target"), ltp, ltp)
        if hit and not self.has_pending_order:
            reason, _ = hit
            self.logger.warning(
                "🛡️ %s hit at %.2f (stop=%s target=%s)", reason, ltp, self.state.get("stop"), self.state.get("target")
            )
            await self.submit(
                Side.SELL if self.position.qty > 0 else Side.BUY, abs(self.position.qty), reason=reason, is_exit=True
            )

    async def flatten(self, reason: str) -> bool:
        """Cancels working orders and closes the position at market. Returns True if flat."""
        async with self._lock:
            return await self._flatten_locked(reason)

    async def _flatten_locked(self, reason: str) -> bool:
        oms = self._deps.oms
        await oms.cancel_all(self.instance_id)
        if not await oms.wait_until_idle(self.instance_id, timeout=15):
            self.logger.error("Flatten: orders still working after cancel; will retry")
            return False
        qty = self.position.qty
        if qty == 0:
            return True
        side = Side.SELL if qty > 0 else Side.BUY
        await oms.submit(
            instance_id=self.instance_id,
            symbol=self.symbol,
            token=self.token,
            side=side,
            qty=abs(qty),
            product=self.cfg.product,
            segment=self.cfg.segment,
            reason=f"FLATTEN:{reason}",
        )
        await oms.wait_until_idle(self.instance_id, timeout=15)
        return self.position.qty == 0

    # ------------------------------------------------------------------ helpers
    async def _guard(self, awaitable: Awaitable[None], where: str) -> None:
        try:
            await awaitable
            self._errors = 0
        except Exception:
            self._errors += 1
            self.logger.exception("Strategy error in %s (%d/%d)", where, self._errors, MAX_CONSECUTIVE_ERRORS)
            if self._errors >= MAX_CONSECUTIVE_ERRORS and not self._warming:
                self._accepting = False
                # Run outside this task: disabling stops (cancels) the runner's own task.
                fire_and_forget(
                    self._deps.on_disable(self.instance_id, f"{self._errors} consecutive errors in {where}")
                )

    async def _persist_state(self) -> None:
        snapshot = json.dumps(self.state, sort_keys=True, default=str)
        if snapshot != self._saved_state:
            await self._deps.save_state(self.instance_id, json.loads(snapshot))
            self._saved_state = snapshot
