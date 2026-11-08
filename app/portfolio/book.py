"""
PositionBook: per-strategy-instance positions and today's P&L, persisted.

A fill, the owning order's cumulative fill state and the instance's net position are
written in ONE transaction, so after a crash the database is always self-consistent and
fills can never be double counted.
"""

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Callable, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.clock import now_ist, start_of_day, today_ist
from app.domain.models import Fill, PositionState, ProductType, Side
from app.models import FillRecord, Order, StrategyInstance
from app.portfolio.charges import ChargesCalculator

logger = logging.getLogger("PositionBook")


@dataclass
class OrderFillUpdate:
    """Order columns persisted together with a fill."""

    order_id: str
    instance_id: str
    token: str
    side: Side
    product: str
    filled_qty: int
    avg_price: float
    status: str
    broker_order_id: Optional[str]
    message: Optional[str]


@dataclass
class DayPnl:
    realized: float = 0.0
    charges: float = 0.0
    unrealized: float = 0.0

    @property
    def net(self) -> float:
        return self.realized - self.charges + self.unrealized


class PositionBook:
    def __init__(self, session_factory: async_sessionmaker, charges: ChargesCalculator):
        self._sf = session_factory
        self._charges = charges
        self._positions: dict[str, PositionState] = {}
        self._tokens: dict[str, str] = {}
        self._active: set[str] = set()
        self._day: date = today_ist()
        self._realized: dict[str, float] = {}
        self._charges_today: dict[str, float] = {}

    async def load(self) -> None:
        async with self._sf() as db:
            rows = (await db.execute(select(StrategyInstance))).scalars().all()
            for r in rows:
                self._positions[r.id] = PositionState(qty=r.position_qty or 0, avg_price=r.avg_price or 0.0)
                self._tokens[r.id] = r.token

            self._day = today_ist()
            stmt = (
                select(FillRecord.instance_id, func.sum(FillRecord.realized_pnl), func.sum(FillRecord.charges))
                .where(FillRecord.ts >= start_of_day(self._day))
                .group_by(FillRecord.instance_id)
            )
            for instance_id, realized, charges in (await db.execute(stmt)).all():
                self._realized[instance_id] = float(realized or 0.0)
                self._charges_today[instance_id] = float(charges or 0.0)

        open_count = sum(1 for p in self._positions.values() if p.qty)
        logger.info("📒 Positions loaded: %d instances, %d open", len(self._positions), open_count)

    def register(self, instance_id: str, token: str) -> None:
        """Marks an instance as actively running (used for reconciliation ownership)."""
        self._positions.setdefault(instance_id, PositionState())
        self._tokens[instance_id] = token
        self._active.add(instance_id)

    def unregister(self, instance_id: str) -> None:
        self._active.discard(instance_id)

    def registered_ids(self) -> set[str]:
        return set(self._active)

    def get(self, instance_id: str) -> PositionState:
        return self._positions.setdefault(instance_id, PositionState())

    def open_positions(self) -> dict[str, PositionState]:
        return {k: v for k, v in self._positions.items() if v.qty != 0}

    def token_of(self, instance_id: str) -> Optional[str]:
        return self._tokens.get(instance_id)

    # ------------------------------------------------------------------ writes
    async def record_fill(self, upd: OrderFillUpdate, qty: int, price: float, ts: Optional[datetime] = None) -> Fill:
        self._roll_day()
        ts = ts or now_ist()
        state = replace(self.get(upd.instance_id))  # mutate a copy; swap in only after commit
        realized = state.apply(upd.side, qty, price)
        charges = self._charges.compute(upd.side, qty, price, ProductType(upd.product))

        async with self._sf() as db, db.begin():
            db.add(
                FillRecord(
                    order_id=upd.order_id,
                    instance_id=upd.instance_id,
                    token=upd.token,
                    side=upd.side.value,
                    qty=qty,
                    price=price,
                    realized_pnl=realized,
                    charges=charges,
                    ts=ts,
                )
            )
            await db.execute(
                update(Order)
                .where(Order.id == upd.order_id)
                .values(
                    filled_qty=upd.filled_qty,
                    avg_price=upd.avg_price,
                    status=upd.status,
                    broker_order_id=upd.broker_order_id,
                    message=upd.message,
                )
            )
            await db.execute(
                update(StrategyInstance)
                .where(StrategyInstance.id == upd.instance_id)
                .values(position_qty=state.qty, avg_price=state.avg_price)
            )

        self._positions[upd.instance_id] = state
        self._tokens.setdefault(upd.instance_id, upd.token)
        self._realized[upd.instance_id] = self._realized.get(upd.instance_id, 0.0) + realized
        self._charges_today[upd.instance_id] = self._charges_today.get(upd.instance_id, 0.0) + charges
        logger.info(
            "💹 Fill %s %s %d @ %.2f | pos=%d avg=%.2f realized=%.2f charges=%.2f",
            upd.instance_id,
            upd.side,
            qty,
            price,
            state.qty,
            state.avg_price,
            realized,
            charges,
        )
        return Fill(upd.order_id, upd.instance_id, upd.token, upd.side, qty, price, ts, realized, charges)

    async def adopt(self, instance_id: str, qty: int, avg_price: float) -> None:
        """Overrides our position with the broker's truth (reconciliation)."""
        async with self._sf() as db, db.begin():
            await db.execute(
                update(StrategyInstance)
                .where(StrategyInstance.id == instance_id)
                .values(position_qty=qty, avg_price=avg_price)
            )
        self._positions[instance_id] = PositionState(qty=qty, avg_price=avg_price if qty else 0.0)

    # ------------------------------------------------------------------ P&L
    def day_pnl(self, mark: Callable[[str], Optional[float]], instance_id: Optional[str] = None) -> DayPnl:
        self._roll_day()
        ids = [instance_id] if instance_id else set(self._positions) | set(self._realized)
        pnl = DayPnl()
        for iid in ids:
            pnl.realized += self._realized.get(iid, 0.0)
            pnl.charges += self._charges_today.get(iid, 0.0)
            pos = self._positions.get(iid)
            token = self._tokens.get(iid)
            if pos and pos.qty and token:
                price = mark(token)
                if price:
                    pnl.unrealized += pos.unrealized(price)
        return pnl

    def _roll_day(self) -> None:
        today = today_ist()
        if today != self._day:
            self._day = today
            self._realized.clear()
            self._charges_today.clear()
