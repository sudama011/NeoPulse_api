"""Backtest metrics computed from fills (round trips are rebuilt from position changes)."""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.domain.models import Fill


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str
    qty: int
    pnl: float  # net of charges


@dataclass
class BacktestReport:
    initial_capital: float
    final_equity: float
    total_charges: float
    trades: list[Trade]
    equity: pd.Series
    fills: list[Fill] = field(repr=False, default_factory=list)

    @property
    def net_pnl(self) -> float:
        return self.final_equity - self.initial_capital

    @property
    def return_pct(self) -> float:
        return self.net_pnl / self.initial_capital * 100

    @property
    def wins(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl > 0]

    @property
    def losses(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl <= 0]

    @property
    def win_rate(self) -> float:
        return len(self.wins) / len(self.trades) * 100 if self.trades else 0.0

    @property
    def profit_factor(self) -> float:
        loss = -sum(t.pnl for t in self.losses)
        gain = sum(t.pnl for t in self.wins)
        return gain / loss if loss > 0 else float("inf") if gain > 0 else 0.0

    @property
    def expectancy(self) -> float:
        return sum(t.pnl for t in self.trades) / len(self.trades) if self.trades else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        if self.equity.empty:
            return 0.0
        peak = self.equity.cummax()
        return float(((self.equity - peak) / peak).min() * 100)

    @property
    def sharpe(self) -> float:
        if self.equity.empty:
            return 0.0
        daily = self.equity.groupby(self.equity.index.map(lambda t: t.date())).last().pct_change().dropna()
        if len(daily) < 2 or daily.std() == 0:
            return 0.0
        return float(daily.mean() / daily.std() * np.sqrt(252))

    def fills_as_dicts(self) -> list[dict]:
        return [{"time": f.ts, "side": f.side.value, "qty": f.qty, "price": f.price} for f in self.fills]


def analyse(initial_capital: float, fills: list[Fill], equity: pd.Series) -> BacktestReport:
    trades: list[Trade] = []
    pos, pnl, entry_time, direction, peak_qty = 0, 0.0, None, "", 0
    for f in fills:
        if pos == 0:
            entry_time, direction, pnl, peak_qty = f.ts, "LONG" if f.side.value == "BUY" else "SHORT", 0.0, 0
        pos += f.qty if f.side.value == "BUY" else -f.qty
        peak_qty = max(peak_qty, abs(pos))
        pnl += f.realized_pnl - f.charges
        if pos == 0:
            trades.append(Trade(entry_time, f.ts, direction, peak_qty, round(pnl, 2)))

    final = float(equity.iloc[-1]) if not equity.empty else initial_capital
    return BacktestReport(
        initial_capital=initial_capital,
        final_equity=final,
        total_charges=round(sum(f.charges for f in fills), 2),
        trades=trades,
        equity=equity,
        fills=fills,
    )


def print_report(r: BacktestReport, title: str = "") -> None:
    pf = r.profit_factor
    print("\n" + "=" * 56)
    print(f"  BACKTEST  {title}")
    print("=" * 56)
    print(f"  Capital            {r.initial_capital:>14,.2f}")
    print(f"  Final equity       {r.final_equity:>14,.2f}  ({r.return_pct:+.2f}%)")
    print(f"  Net P&L            {r.net_pnl:>14,.2f}  (charges {r.total_charges:,.2f})")
    print("-" * 56)
    print(f"  Trades             {len(r.trades):>14}")
    print(f"  Win rate           {r.win_rate:>13.1f}%")
    print(f"  Profit factor      {'inf' if pf == float('inf') else f'{pf:.2f}':>14}")
    print(f"  Expectancy/trade   {r.expectancy:>14,.2f}")
    print(f"  Max drawdown       {r.max_drawdown_pct:>13.2f}%")
    print(f"  Sharpe (daily)     {r.sharpe:>14.2f}")
    print("=" * 56)
