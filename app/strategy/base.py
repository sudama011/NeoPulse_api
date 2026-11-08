"""
Strategy SDK.

A strategy only talks to its `StrategyContext`, which is implemented by the live runner
(`app/strategy/runner.py`) and the backtester (`backtest/engine.py`). The same strategy
code therefore runs unchanged in live, paper and backtest modes.

Lifecycle (driven by the context):
    setup()            parse params (constructor)
    on_candle(c)       every CLOSED candle of `timeframe` minutes, after `warmup` candles
    on_tick(t)         every tick (live) / candle close (backtest); optional
    on_fill(f)         after each execution of this strategy's orders; optional

Protective stop/target: call `self.set_protection(stop=..., target=...)` (or pass them to
buy()/sell()). The context monitors them on every price update and exits at market when
hit; they are persisted, so they survive restarts.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime
from typing import Any, ClassVar, Optional, Protocol

from app.domain.models import Candle, Fill, OrderType, PositionState, Side, Tick


class StrategyContext(Protocol):
    instance_id: str
    symbol: str
    token: str
    timeframe: int
    state: dict[str, Any]  # persisted strategy state (JSON-serialisable values only)
    logger: logging.Logger

    @property
    def position(self) -> PositionState: ...

    @property
    def has_pending_order(self) -> bool: ...

    @property
    def is_warming_up(self) -> bool: ...

    def now(self) -> datetime: ...

    def size(self, entry: float, stop: Optional[float], confidence: float) -> int: ...

    async def submit(
        self,
        side: Side,
        qty: int,
        *,
        reason: str,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        is_exit: bool = False,
    ) -> bool: ...


class Strategy(ABC):
    warmup: int = 50  # closed candles required before on_candle is called (may be set in setup)
    history_size: ClassVar[int] = 500

    def __init__(self, ctx: StrategyContext, params: dict[str, Any]):
        self.ctx = ctx
        self.params = dict(params or {})
        self.log = ctx.logger
        self.setup()  # may override self.warmup from params
        self.candles: deque[Candle] = deque(maxlen=max(self.history_size, self.warmup + 1))

    # ------------------------------------------------------------------ hooks
    def setup(self) -> None:
        """Read `self.params` here."""

    @abstractmethod
    async def on_candle(self, candle: Candle) -> None: ...

    async def on_tick(self, tick: Tick) -> None:
        """Optional tick-level logic."""

    async def on_fill(self, fill: Fill) -> None:
        """Optional fill notification."""

    # ------------------------------------------------------------------ framework entry
    async def process_candle(self, candle: Candle) -> None:
        self.candles.append(candle)
        if len(self.candles) >= self.warmup:
            await self.on_candle(candle)

    # ------------------------------------------------------------------ state helpers
    @property
    def position(self) -> int:
        return self.ctx.position.qty

    @property
    def avg_price(self) -> float:
        return self.ctx.position.avg_price

    @property
    def state(self) -> dict[str, Any]:
        return self.ctx.state

    @property
    def stop(self) -> Optional[float]:
        return self.ctx.state.get("stop")

    @property
    def target(self) -> Optional[float]:
        return self.ctx.state.get("target")

    def set_protection(self, stop: Optional[float] = None, target: Optional[float] = None) -> None:
        self.ctx.state["stop"] = round(stop, 2) if stop else None
        self.ctx.state["target"] = round(target, 2) if target else None

    # ------------------------------------------------------------------ order helpers
    async def buy(
        self,
        price: float,
        qty: Optional[int] = None,
        *,
        stop: Optional[float] = None,
        target: Optional[float] = None,
        confidence: float = 1.0,
        reason: str = "BUY",
        limit: bool = False,
    ) -> bool:
        """Covers a short if short; otherwise opens/adds to a long."""
        return await self._trade(Side.BUY, price, qty, stop, target, confidence, reason, limit)

    async def sell(
        self,
        price: float,
        qty: Optional[int] = None,
        *,
        stop: Optional[float] = None,
        target: Optional[float] = None,
        confidence: float = 1.0,
        reason: str = "SELL",
        limit: bool = False,
    ) -> bool:
        """Closes a long if long; otherwise opens/adds to a short."""
        return await self._trade(Side.SELL, price, qty, stop, target, confidence, reason, limit)

    async def exit(self, reason: str = "EXIT") -> bool:
        pos = self.position
        if pos == 0:
            return False
        side = Side.SELL if pos > 0 else Side.BUY
        return await self.ctx.submit(side, abs(pos), reason=reason, is_exit=True)

    async def _trade(self, side, price, qty, stop, target, confidence, reason, limit) -> bool:
        pos = self.position
        closing = pos != 0 and (pos > 0) != (side is Side.BUY)
        if closing:
            qty = min(qty or abs(pos), abs(pos))
            return await self.ctx.submit(side, qty, reason=reason, is_exit=True)

        if qty is None:
            qty = self.ctx.size(price, stop, confidence)
        if qty <= 0:
            self.log.info("Signal %s ignored: computed quantity is 0", reason)
            return False

        ok = await self.ctx.submit(
            side,
            qty,
            reason=reason,
            order_type=OrderType.LIMIT if limit else OrderType.MARKET,
            price=price if limit else 0.0,
        )
        if ok and (stop or target):
            self.set_protection(stop, target)
        return ok


def protective_exit(
    position: int,
    stop: Optional[float],
    target: Optional[float],
    high: float,
    low: float,
    open_: Optional[float] = None,
) -> Optional[tuple[str, float]]:
    """
    Decides whether a stop or target was hit within a price range [low, high].
    Returns (reason, reference_price) or None. Stops win ties (conservative).
    For backtests `open_` lets gaps through the stop fill at the open, not at the stop.
    """
    if position == 0:
        return None
    if position > 0:
        if stop and low <= stop:
            return "STOP_LOSS", min(stop, open_) if open_ is not None else stop
        if target and high >= target:
            return "TARGET", max(target, open_) if open_ is not None else target
    else:
        if stop and high >= stop:
            return "STOP_LOSS", max(stop, open_) if open_ is not None else stop
        if target and low <= target:
            return "TARGET", min(target, open_) if open_ is not None else target
    return None
