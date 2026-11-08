"""
StrategyEngine: owns the set of strategy instances and their runners.

- Starts every ACTIVE instance from the database on boot (restart = resume).
- Supervises: an instance that fails to start (e.g. broker briefly down) is retried.
- Provides account-wide actions: flatten all, intraday square-off, disable on errors.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional, cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.clock import now_ist
from app.domain.models import ProductType, Side
from app.market.bus import TickBus
from app.market.feed import MarketFeed
from app.market.history import HistoryService, supported_timeframe
from app.market.instruments import InstrumentStore
from app.models import StrategyInstance
from app.notifications.notifier import Notifier
from app.oms.order_manager import OrderManager
from app.portfolio.book import PositionBook
from app.risk.manager import RiskManager
from app.strategy import get_strategy_class
from app.strategy.base import StrategyContext
from app.strategy.runner import InstanceConfig, RunnerDeps, StrategyRunner

logger = logging.getLogger("StrategyEngine")

ACTIVE, STOPPED, DISABLED = "ACTIVE", "STOPPED", "DISABLED"


class EngineError(ValueError):
    """Invalid request (bad strategy, symbol, duplicate instance, ...)."""


@dataclass
class NewInstance:
    strategy_type: str
    symbol: str
    capital: float
    params: dict[str, Any]
    timeframe: int = 5
    product: ProductType = ProductType.MIS
    risk_per_trade_pct: float = 0.01
    leverage: float = 1.0
    name: Optional[str] = None


class StrategyEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        oms: OrderManager,
        positions: PositionBook,
        risk: RiskManager,
        bus: TickBus,
        history: HistoryService,
        instruments: InstrumentStore,
        feed: MarketFeed,
        notifier: Notifier,
    ):
        self._sf = session_factory
        self._instruments = instruments
        self._feed = feed
        self._notifier = notifier
        self._deps = RunnerDeps(
            oms=oms,
            positions=positions,
            risk=risk,
            bus=bus,
            history=history,
            save_state=self._save_state,
            on_disable=self.disable,
            alert=lambda message: notifier.send(message, level="ERROR"),
        )
        self.runners: dict[str, StrategyRunner] = {}
        self._failed: dict[str, str] = {}  # instance_id -> last start error
        self._lock = asyncio.Lock()

    @property
    def oms(self) -> OrderManager:
        return self._deps.oms

    @property
    def positions(self) -> PositionBook:
        return self._deps.positions

    # ------------------------------------------------------------------ boot & supervision
    async def start_all(self) -> None:
        """Starts every ACTIVE instance that is not running (also the periodic supervisor)."""
        async with self._sf() as db:
            rows = (await db.execute(select(StrategyInstance).where(StrategyInstance.status == ACTIVE))).scalars().all()
        for row in rows:
            if row.id not in self.runners:
                await self._start_runner(row)

    async def register_active(self) -> None:
        """Marks ACTIVE instances as owners of their tokens (before runners start)."""
        async with self._sf() as db:
            rows = (await db.execute(select(StrategyInstance).where(StrategyInstance.status == ACTIVE))).scalars().all()
        for row in rows:
            self.positions.register(row.id, row.token)

    async def tick_timers(self) -> None:
        now = now_ist()
        for runner in list(self.runners.values()):
            await runner.on_timer(now)

    async def shutdown(self) -> None:
        """Stops runners WITHOUT touching positions (a restart resumes them)."""
        for runner in list(self.runners.values()):
            await runner.stop()
        self.runners.clear()

    async def _start_runner(self, row: StrategyInstance) -> bool:
        try:
            cls = get_strategy_class(row.strategy_type)
            instrument = self._instruments.by_token(row.token) or self._instruments.get(row.symbol)
            if instrument is None:
                raise EngineError(f"instrument {row.symbol} not in master (run instrument sync)")
            cfg = _to_config(row)
            self._deps.positions.register(row.id, row.token)
            runner = StrategyRunner(cfg, cls, instrument, self._deps)
            await self._feed.subscribe([row.token])
            await runner.start()
            self.runners[row.id] = runner
            self._failed.pop(row.id, None)
            return True
        except Exception as e:
            first_failure = row.id not in self._failed
            self._failed[row.id] = str(e)
            if first_failure:
                logger.exception("Failed to start %s; will retry every 30s", row.name)
                self._notifier.send(f"Strategy {row.name} failed to start: {e}", level="ERROR")
            else:
                logger.warning("Start retry failed for %s: %s", row.name, e)
            return False

    # ------------------------------------------------------------------ instance management
    async def create(self, req: NewInstance) -> StrategyInstance:
        cls = get_strategy_class(req.strategy_type)  # raises ValueError for unknown names
        instrument = self._instruments.get(req.symbol)
        if instrument is None:
            raise EngineError(f"Unknown symbol '{req.symbol}'")
        if not supported_timeframe(req.timeframe):
            raise EngineError(f"Unsupported timeframe {req.timeframe}m")
        if req.capital <= 0:
            raise EngineError("capital must be positive")

        async with self._lock:
            async with self._sf() as db:
                clash = (
                    (
                        await db.execute(
                            select(StrategyInstance).where(
                                StrategyInstance.token == instrument.token, StrategyInstance.status == ACTIVE
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
            if clash:
                raise EngineError(f"{instrument.trading_symbol} already has an active strategy ({clash.name})")

            row = StrategyInstance(
                id=uuid.uuid4().hex[:12],
                name=req.name or f"{cls.__name__}-{instrument.trading_symbol}",
                strategy_type=req.strategy_type.upper(),
                symbol=instrument.trading_symbol,
                token=instrument.token,
                segment=instrument.segment,
                product=req.product.value,
                timeframe=req.timeframe,
                params=req.params,
                capital=req.capital,
                risk_per_trade_pct=req.risk_per_trade_pct,
                leverage=req.leverage,
                status=ACTIVE,
                position_qty=0,
                avg_price=0.0,
                state={},
            )
            # Validate params by constructing the strategy before persisting.
            cls(cast(StrategyContext, _DryRunContext(row)), req.params)
            async with self._sf() as db, db.begin():
                db.add(row)

        await self._start_runner(row)
        logger.info("➕ Created %s (%s on %s)", row.name, row.strategy_type, row.symbol)
        return row

    async def stop(self, instance_id: str, flatten: bool = True, status: str = STOPPED, reason: str = "") -> bool:
        """Stops an instance. With flatten=True its position is closed first. Returns True if flat."""
        runner = self.runners.get(instance_id)
        is_flat = True
        if flatten:
            is_flat = await (runner.flatten(reason or "stop") if runner else self._liquidate(instance_id, "stop"))
        self.positions.unregister(instance_id)
        if runner:
            await runner.stop()
            self.runners.pop(instance_id, None)
            await self._feed.unsubscribe([runner.token])
        self._failed.pop(instance_id, None)
        async with self._sf() as db, db.begin():
            await db.execute(
                update(StrategyInstance)
                .where(StrategyInstance.id == instance_id)
                .values(status=status, status_reason=reason or None)
            )
        if not is_flat:
            self._notifier.send(f"Instance {instance_id} stopped but position is NOT flat", level="CRITICAL")
        return is_flat

    async def resume(self, instance_id: str) -> None:
        async with self._sf() as db:
            row = await db.get(StrategyInstance, instance_id)
            if row is None:
                raise EngineError("instance not found")
            clash = (
                (
                    await db.execute(
                        select(StrategyInstance).where(
                            StrategyInstance.token == row.token,
                            StrategyInstance.status == ACTIVE,
                            StrategyInstance.id != instance_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
        if clash:
            raise EngineError(f"{row.symbol} already has an active strategy ({clash.name})")
        async with self._sf() as db, db.begin():
            await db.execute(
                update(StrategyInstance)
                .where(StrategyInstance.id == instance_id)
                .values(status=ACTIVE, status_reason=None)
            )
            row = await db.get(StrategyInstance, instance_id)
        await self._start_runner(row)

    async def disable(self, instance_id: str, reason: str) -> None:
        logger.critical("⛔ Disabling %s: %s", instance_id, reason)
        flat = await self.stop(instance_id, flatten=True, status=DISABLED, reason=reason)
        self._notifier.send(f"Strategy {instance_id} DISABLED: {reason}. Flat={flat}", level="CRITICAL")

    # ------------------------------------------------------------------ account actions
    async def flatten_all(self, reason: str) -> None:
        """Closes every position this system owns (running or orphaned)."""
        logger.warning("🧹 Flatten all: %s", reason)
        results = await asyncio.gather(
            *(r.flatten(reason) for r in self.runners.values()),
            *(self._liquidate(iid, reason) for iid in self.positions.open_positions() if iid not in self.runners),
            return_exceptions=True,
        )
        failures = [r for r in results if r is not True]
        if failures:
            self._notifier.send(f"Flatten all ({reason}): {len(failures)} position(s) NOT closed", level="CRITICAL")

    async def square_off_intraday(self) -> None:
        """End-of-day exit for MIS positions (avoids broker auto square-off). Raises if incomplete."""
        runners = [r for r in self.runners.values() if r.cfg.product is ProductType.MIS and r.position.qty != 0]
        orphans = [iid for iid in self.positions.open_positions() if iid not in self.runners]
        if not runners and not orphans:
            return
        logger.warning("⏰ Intraday square-off: %d running + %d orphaned", len(runners), len(orphans))
        names = [r.cfg.name for r in runners] + orphans
        results = await asyncio.gather(
            *(r.flatten("EOD_SQUARE_OFF") for r in runners),
            *(self._liquidate(iid, "EOD_SQUARE_OFF", only_product=ProductType.MIS) for iid in orphans),
            return_exceptions=True,
        )
        failed = [n for n, ok in zip(names, results) if ok is not True]
        if failed:
            self._notifier.send(f"Square-off incomplete for: {', '.join(failed)}", level="CRITICAL")
            raise RuntimeError(f"square-off incomplete: {failed}")  # scheduler retries until market close
        self._notifier.send(f"Square-off done for {len(names)} position(s)", level="SUCCESS")

    async def _liquidate(self, instance_id: str, reason: str, only_product: Optional[ProductType] = None) -> bool:
        """Closes a position that has no running strategy."""
        pos = self.positions.get(instance_id)
        if pos.qty == 0:
            return True
        async with self._sf() as db:
            row = await db.get(StrategyInstance, instance_id)
        if row is None:
            return False
        if only_product is not None and row.product != only_product.value:
            return True
        await self.oms.cancel_all(instance_id)
        await self.oms.wait_until_idle(instance_id)
        await self.oms.submit(
            instance_id=instance_id,
            symbol=row.symbol,
            token=row.token,
            side=Side.SELL if pos.qty > 0 else Side.BUY,
            qty=abs(pos.qty),
            product=ProductType(row.product),
            segment=row.segment,
            reason=f"LIQUIDATE:{reason}",
        )
        await self.oms.wait_until_idle(instance_id)
        return self.positions.get(instance_id).qty == 0

    # ------------------------------------------------------------------ persistence
    async def _save_state(self, instance_id: str, state: dict) -> None:
        async with self._sf() as db, db.begin():
            await db.execute(update(StrategyInstance).where(StrategyInstance.id == instance_id).values(state=state))

    def failures(self) -> dict[str, str]:
        return dict(self._failed)


def _to_config(row: StrategyInstance) -> InstanceConfig:
    return InstanceConfig(
        id=row.id,
        name=row.name,
        strategy_type=row.strategy_type,
        symbol=row.symbol,
        token=row.token,
        segment=row.segment or "nse_cm",
        product=ProductType(row.product),
        timeframe=row.timeframe,
        params=row.params or {},
        capital=row.capital,
        risk_per_trade_pct=row.risk_per_trade_pct,
        leverage=row.leverage,
        state=row.state or {},
    )


class _DryRunContext:
    """Minimal context used only to validate strategy params at creation time."""

    def __init__(self, row: StrategyInstance):
        self.instance_id, self.symbol, self.token, self.timeframe = row.id, row.symbol, row.token, row.timeframe
        self.state: dict = {}
        self.logger = logging.getLogger(f"strategy.{row.name}")
