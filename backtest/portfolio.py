"""
Portfolio backtest: one strategy instance per symbol, capital split equally, equity curves
summed. Reports CAGR, Sharpe, max drawdown, both for the whole period and split into
in-sample / out-of-sample at --split (rules must not be tuned on the out-of-sample part).

    python -m backtest.portfolio --strategy RSI2_PULLBACK --interval 1440 --days 3650 \
        --symbols NIFTY50 --product CNC --split 2022-01-01
"""

import argparse
import asyncio
import json
import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from app.domain.models import Candle, ProductType
from backtest.analyst import analyse
from backtest.engine import BacktestConfig, SymbolSimulator
from backtest.feed import load_candles

logger = logging.getLogger("Portfolio")

# Liquid large caps (today's constituents: survivorship bias applies to long look-backs).
NIFTY50 = [
    "RELIANCE",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "TCS",
    "BHARTIARTL",
    "ITC",
    "SBIN",
    "LT",
    "KOTAKBANK",
    "AXISBANK",
    "HINDUNILVR",
    "BAJFINANCE",
    "MARUTI",
    "SUNPHARMA",
    "TITAN",
    "ASIANPAINT",
    "ULTRACEMCO",
    "NTPC",
    "POWERGRID",
    "M&M",
    "TATASTEEL",
    "HCLTECH",
    "WIPRO",
    "NESTLEIND",
    "JSWSTEEL",
    "ONGC",
    "COALINDIA",
    "TECHM",
    "DRREDDY",
    "CIPLA",
    "GRASIM",
    "HINDALCO",
    "ADANIPORTS",
    "BAJAJFINSV",
    "EICHERMOT",
    "BRITANNIA",
    "HEROMOTOCO",
    "DIVISLAB",
    "APOLLOHOSP",
]
UNIVERSES = {"NIFTY50": NIFTY50}


@dataclass
class Metrics:
    start: date
    end: date
    cagr: float
    sharpe: float
    max_drawdown: float
    total_return: float
    trades: int
    win_rate: float
    exposure: float  # avg fraction of capital-days with an open position (approx.)

    def row(self, label: str) -> str:
        return (
            f"{label:<14} {self.start} → {self.end}  CAGR {self.cagr:7.2%}  Sharpe {self.sharpe:5.2f}  "
            f"MaxDD {self.max_drawdown:7.2%}  Return {self.total_return:7.2%}  Trades {self.trades:5d}  "
            f"Win {self.win_rate:5.1%}"
        )

    @property
    def meets_target(self) -> bool:
        return self.cagr >= 0.20 and self.sharpe > 3 and self.max_drawdown > -0.15


def metrics(equity: pd.Series, trades: list, exposure: float = float("nan")) -> Metrics:
    daily = equity.groupby(equity.index.normalize()).last()
    rets = daily.pct_change().dropna()
    years = max((daily.index[-1] - daily.index[0]).days / 365.25, 1e-9)
    total = daily.iloc[-1] / daily.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    sharpe = float(rets.mean() / rets.std() * math.sqrt(252)) if len(rets) > 2 and rets.std() > 0 else 0.0
    dd = float((daily / daily.cummax() - 1).min())
    wins = sum(1 for t in trades if t.pnl > 0)
    return Metrics(
        daily.index[0].date(),
        daily.index[-1].date(),
        cagr,
        sharpe,
        dd,
        total,
        len(trades),
        wins / len(trades) if trades else 0.0,
        exposure,
    )


async def run_portfolio(
    symbols: list[str],
    strategy: str,
    interval: int,
    days: int,
    capital: float,
    params: dict,
    product: ProductType,
    leverage: float = 1.0,
    risk_pct: float = 1.0,
    max_positions: int = 5,
    source: str = "yahoo",
) -> tuple[pd.Series, list]:
    """
    Time-synchronised multi-symbol simulation mirroring live risk rules: each instance gets
    capital / max_positions, and at most `max_positions` instances may hold (or be entering)
    a position at once, exactly like MAX_OPEN_POSITIONS in the live RiskManager.
    """
    slice_capital = capital / max_positions
    data: dict[str, list[Candle]] = {}
    if source == "kotak":
        from backtest.kotak_feed import load_kotak_candles  # needs credentials + instrument master

        data = await load_kotak_candles(symbols, days, interval)
    else:
        for sym in symbols:
            try:
                data[sym] = load_candles(sym, days, interval)[0]
            except Exception as e:
                logger.warning("skip %s: %s", sym, e)
    if not data:
        raise RuntimeError("no data")

    sims: dict[str, SymbolSimulator] = {}

    def gate(sim: SymbolSimulator) -> bool:
        busy = sum(1 for s in sims.values() if s is not sim and (s.position != 0 or s.wants_entry))
        return busy < max_positions

    for sym in data:
        cfg = BacktestConfig(
            symbol=sym,
            strategy=strategy,
            params=params,
            capital=slice_capital,
            product=product,
            timeframe=min(interval, 375),
            leverage=leverage,
            risk_per_trade_pct=risk_pct,
        )
        sims[sym] = SymbolSimulator(cfg, entry_gate=gate)

    events: dict[pd.Timestamp, list[tuple[str, Candle, bool]]] = {}
    for sym, candles in data.items():
        for i, c in enumerate(candles):
            session_end = i == len(candles) - 1 or candles[i + 1].start.date() != c.start.date()
            events.setdefault(pd.Timestamp(c.start), []).append((sym, c, session_end))

    equity: dict[pd.Timestamp, float] = {}
    for ts in sorted(events):
        for sym, candle, session_end in events[ts]:
            await sims[sym].step(candle, session_end)
        equity[ts] = capital + sum(s.pnl() for s in sims.values())

    for sym, sim in sims.items():
        await sim.finish(data[sym][-1])
    last = max(events)
    equity[last] = capital + sum(s.pnl() for s in sims.values())

    trades: list = []
    for sym, sim in sims.items():
        report = analyse(slice_capital, sim.ctx.fills, pd.Series(dtype=float))
        trades += [(sym, t) for t in report.trades]
    return pd.Series(equity), trades


def split_report(equity: pd.Series, trades: list, split: Optional[str]) -> list[tuple[str, Metrics]]:
    out = [("FULL", metrics(equity, [t for _, t in trades]))]
    if split:
        cut = pd.Timestamp(split)
        cut = cut.tz_localize(equity.index.tz) if cut.tzinfo is None else cut
        ins, oos = equity[equity.index < cut], equity[equity.index >= cut]
        if len(ins) > 5:
            out.append(("IN-SAMPLE", metrics(ins, [t for _, t in trades if t.exit_time < cut])))
        if len(oos) > 5:
            out.append(("OUT-OF-SAMPLE", metrics(oos, [t for _, t in trades if t.entry_time >= cut])))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Portfolio backtest")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--symbols", default="NIFTY50", help="comma list or a universe name")
    ap.add_argument("--interval", type=int, default=1440, help="minutes; 1440 = daily")
    ap.add_argument("--days", type=int, default=3650)
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--product", default="CNC", choices=["CNC", "MIS"])
    ap.add_argument("--leverage", type=float, default=1.0)
    ap.add_argument("--risk", type=float, default=1.0, help="risk per trade (fraction of instance capital)")
    ap.add_argument("--max-positions", type=int, default=5)
    ap.add_argument("--source", default="yahoo", choices=["yahoo", "kotak"], help="kotak: longer intraday history")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--split", default=None, help="YYYY-MM-DD in/out-of-sample boundary")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    symbols = UNIVERSES.get(args.symbols.upper()) or [s.strip() for s in args.symbols.split(",") if s.strip()]
    equity, trades = asyncio.run(
        run_portfolio(
            symbols,
            args.strategy,
            args.interval,
            args.days,
            args.capital,
            json.loads(args.params),
            ProductType(args.product),
            args.leverage,
            args.risk,
            args.max_positions,
            args.source,
        )
    )
    print(
        f"\n{args.strategy} · {len(symbols)} symbols · {args.interval}m · {args.product} · "
        f"max_pos {args.max_positions} · lev {args.leverage} · params {args.params}"
    )
    for label, m in split_report(equity, trades, args.split):
        print(m.row(label) + ("  ✅ target" if m.meets_target else ""))
    pnl_by_symbol = pd.Series({s: sum(t.pnl for x, t in trades if x == s) for s in symbols}).sort_values()
    print(f"Worst symbols: {pnl_by_symbol.head(3).round(0).to_dict()}")
    print(f"Best symbols:  {pnl_by_symbol.tail(3).round(0).to_dict()}")
    print(f"Avg trade P&L (net): {np.mean([t.pnl for _, t in trades]) if trades else 0:,.2f}")


if __name__ == "__main__":
    main()
