"""
Event-driven backtester that runs the SAME strategy classes as live trading.

Execution model (no look-ahead):
- Orders submitted while processing candle N fill at candle N+1:
  market -> at its open (+/- slippage); limit -> if its range trades through the limit
  (unfilled limits expire after `limit_ttl_candles`).
- Stop/target are checked against each candle's range; a gap through the stop fills at
  the open (pessimistic), and a stop wins over a target inside the same candle.
- Statutory charges are applied to every fill; intraday positions are squared off at the
  last candle of each session.

Usage:
    python -m backtest.engine --symbol RELIANCE --strategy MACD_VOLUME --days 59 --interval 5
"""

import argparse
import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dtime
from typing import Any, Callable, Optional

import pandas as pd

from app.domain.models import Candle, Fill, OrderType, PositionState, ProductType, Side, Tick
from app.portfolio.charges import ChargesCalculator
from app.risk.sizer import position_size
from app.strategy import get_strategy_class, list_strategies
from app.strategy.base import Strategy, protective_exit
from backtest.analyst import BacktestReport, analyse, print_report
from backtest.feed import load_candles

logger = logging.getLogger("Backtest")


@dataclass
class _Pending:
    side: Side
    qty: int
    order_type: OrderType
    price: float
    reason: str
    placed_index: int


@dataclass
class BacktestConfig:
    symbol: str
    strategy: str
    params: dict[str, Any] = field(default_factory=dict)
    capital: float = 100_000.0
    risk_per_trade_pct: float = 0.01
    leverage: float = 1.0
    product: ProductType = ProductType.MIS
    timeframe: int = 5
    slippage_bps: float = 2.0
    limit_ttl_candles: int = 3
    lot_size: int = 1
    square_off: dtime = dtime(15, 15)  # MIS positions are closed at the open of the first bar at/after this


class BacktestContext:
    """StrategyContext implementation backed by a simulated broker."""

    def __init__(self, cfg: BacktestConfig):
        self.cfg = cfg
        self.instance_id = "backtest"
        self.symbol = cfg.symbol
        self.token = "0"
        self.timeframe = cfg.timeframe
        self.state: dict[str, Any] = {}
        self.logger = logging.getLogger(f"strategy.backtest.{cfg.strategy}")
        self._position = PositionState()
        self._pending: Optional[_Pending] = None
        self._now: datetime = datetime.min
        self._index = 0
        self._charges = ChargesCalculator()
        self.fills: list[Fill] = []
        self.entry_gate: Optional[Callable[[], bool]] = None  # portfolio-level limit (e.g. max positions)
        self._pending_is_entry = False

    # ------------------------------------------------------------------ StrategyContext
    @property
    def position(self) -> PositionState:
        return self._position

    @property
    def has_pending_order(self) -> bool:
        return self._pending is not None

    @property
    def is_warming_up(self) -> bool:
        return False

    def now(self) -> datetime:
        return self._now

    def size(self, entry: float, stop: Optional[float], confidence: float) -> int:
        return position_size(
            capital=self.cfg.capital,
            entry=entry,
            stop=stop,
            risk_per_trade_pct=self.cfg.risk_per_trade_pct,
            leverage=self.cfg.leverage,
            confidence=confidence,
            lot_size=self.cfg.lot_size,
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
        if self._pending is not None or qty <= 0:
            return False
        pos = self._position.qty
        is_entry = not is_exit and not (pos != 0 and (pos > 0) != (side is Side.BUY) and qty <= abs(pos))
        if is_entry and self.entry_gate is not None and not self.entry_gate():
            return False
        self._pending = _Pending(side, qty, order_type, price, reason, self._index)
        self._pending_is_entry = is_entry
        return True

    # ------------------------------------------------------------------ simulation
    def _fill(self, side: Side, qty: int, price: float, ts: datetime) -> Fill:
        price = round(price, 2)
        realized = self._position.apply(side, qty, price)
        charges = self._charges.compute(side, qty, price, self.cfg.product)
        fill = Fill("bt", self.instance_id, self.token, side, qty, price, ts, realized, charges)
        self.fills.append(fill)
        if self._position.qty == 0:
            self.state.pop("stop", None)
            self.state.pop("target", None)
        return fill

    def _execute_pending(self, candle: Candle) -> Optional[Fill]:
        p = self._pending
        if p is None:
            return None
        slip = self.cfg.slippage_bps / 10_000
        if p.order_type is OrderType.MARKET:
            price = candle.open * (1 + slip if p.side is Side.BUY else 1 - slip)
        else:
            crossed = candle.low <= p.price if p.side is Side.BUY else candle.high >= p.price
            if not crossed:
                if self._index - p.placed_index >= self.cfg.limit_ttl_candles:
                    self._pending = None  # expired
                return None
            price = min(p.price, candle.open) if p.side is Side.BUY else max(p.price, candle.open)
        self._pending = None
        return self._fill(p.side, p.qty, price, candle.start)

    def _check_protection(self, candle: Candle) -> Optional[Fill]:
        hit = protective_exit(
            self._position.qty, self.state.get("stop"), self.state.get("target"), candle.high, candle.low, candle.open
        )
        if hit is None:
            return None
        _, price = hit
        side = Side.SELL if self._position.qty > 0 else Side.BUY
        self._pending = None
        return self._fill(side, abs(self._position.qty), price, candle.start)


class SymbolSimulator:
    """Steps one strategy over one symbol's candles. Used directly and by the portfolio runner."""

    def __init__(self, cfg: BacktestConfig, entry_gate: Optional[Callable[["SymbolSimulator"], bool]] = None):
        self.cfg = cfg
        self.ctx = BacktestContext(cfg)
        self.ctx.entry_gate = (lambda: entry_gate(self)) if entry_gate else None
        self.strategy: Strategy = get_strategy_class(cfg.strategy)(self.ctx, cfg.params)
        self.realized_net = 0.0
        self.last_close: Optional[float] = None
        self._index = 0

    @property
    def position(self) -> int:
        return self.ctx.position.qty

    @property
    def wants_entry(self) -> bool:
        return self.ctx._pending is not None and self.ctx._pending_is_entry

    def pnl(self) -> float:
        """Realized (net of charges) + unrealized P&L at the last close."""
        mark = self.last_close or 0.0
        return self.realized_net + self.ctx.position.unrealized(mark)

    async def step(self, candle: Candle, session_ends: bool) -> None:
        ctx = self.ctx
        ctx._index, ctx._now = self._index, candle.start
        self._index += 1
        if self.cfg.product is ProductType.MIS and candle.start.timetz().replace(tzinfo=None) >= self.cfg.square_off:
            # Live parity: the scheduler squares off intraday positions at SQUARE_OFF_TIME, no new trades after.
            ctx._pending = None
            if ctx.position.qty != 0:
                side = Side.SELL if ctx.position.qty > 0 else Side.BUY
                await self._on_fill(ctx._fill(side, abs(ctx.position.qty), candle.open, candle.start))
            self.last_close = candle.close
            return
        for f in (ctx._execute_pending(candle), ctx._check_protection(candle)):
            if f:
                await self._on_fill(f)

        await self.strategy.process_candle(candle)
        await self.strategy.on_tick(Tick(token="0", ltp=candle.close, ts=candle.start, volume=candle.volume))
        self.last_close = candle.close

        if session_ends and self.cfg.product is ProductType.MIS:
            ctx._pending = None  # intraday orders never carry over to the next session
            await self._close_at(candle)

    async def finish(self, candle: Candle) -> None:
        """Marks the run end: any open position is closed at the last close."""
        await self._close_at(candle)

    async def _close_at(self, candle: Candle) -> None:
        if self.ctx.position.qty != 0:
            side = Side.SELL if self.ctx.position.qty > 0 else Side.BUY
            await self._on_fill(self.ctx._fill(side, abs(self.ctx.position.qty), candle.close, candle.start))

    async def _on_fill(self, f: Fill) -> None:
        self.realized_net += f.realized_pnl - f.charges
        await self.strategy.on_fill(f)


async def run_backtest(cfg: BacktestConfig, candles: list[Candle]) -> BacktestReport:
    if not candles:
        raise ValueError("no candles to backtest")
    sim = SymbolSimulator(cfg)
    equity: list[tuple[datetime, float]] = []
    for i, candle in enumerate(candles):
        session_ends = i == len(candles) - 1 or candles[i + 1].start.date() != candle.start.date()
        await sim.step(candle, session_ends)
        equity.append((candle.start, cfg.capital + sim.pnl()))
    await sim.finish(candles[-1])
    equity[-1] = (candles[-1].start, cfg.capital + sim.pnl())
    return analyse(cfg.capital, sim.ctx.fills, pd.Series(dict(equity)))


def main() -> None:
    parser = argparse.ArgumentParser(description="NeoPulse backtester")
    parser.add_argument("--symbol", default="RELIANCE")
    parser.add_argument("--strategy", default="MACD_VOLUME", help=f"one of {list(list_strategies())}")
    parser.add_argument("--days", type=int, default=59)
    parser.add_argument("--interval", type=int, default=5, help="candle minutes")
    parser.add_argument("--capital", type=float, default=100_000)
    parser.add_argument("--params", default="{}", help="strategy params as JSON")
    parser.add_argument("--product", default="MIS", choices=["MIS", "CNC"])
    parser.add_argument("--plot", action="store_true", help="write an interactive HTML chart")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    candles, frame = load_candles(args.symbol, args.days, args.interval)
    cfg = BacktestConfig(
        symbol=args.symbol,
        strategy=args.strategy,
        params=json.loads(args.params),
        capital=args.capital,
        timeframe=args.interval,
        product=ProductType(args.product),
    )
    report = asyncio.run(run_backtest(cfg, candles))
    print_report(report, title=f"{args.strategy} on {args.symbol} ({args.interval}m, {len(candles)} candles)")

    if args.plot:
        from backtest.plotter import plot_backtest_interactive

        out = os.path.join(os.path.dirname(__file__), "output", f"{args.strategy}_{args.symbol}_{args.interval}m.html")
        path = plot_backtest_interactive(
            frame, report.fills_as_dicts(), title=f"{args.strategy} · {args.symbol}", outfile_html=out
        )
        if path:
            print(f"Chart: {path}")


if __name__ == "__main__":
    main()
