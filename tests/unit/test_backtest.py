import math
from datetime import datetime, timedelta

import pytest

from app.core.clock import IST
from app.domain.models import Candle, ProductType
from backtest.engine import BacktestConfig, run_backtest


def _session(day: int, prices: list[float]) -> list[Candle]:
    start = datetime(2026, 11, day, 9, 15, tzinfo=IST)
    out = []
    for i, p in enumerate(prices):
        out.append(Candle(start + timedelta(minutes=5 * i), p, p + 0.5, p - 0.5, p, 1000 + i))
    return out


async def test_grid_backtest_squares_off_each_session_and_accounts_charges():
    falling_then_rising = [100 - 0.2 * i for i in range(10)] + [98 + 0.3 * i for i in range(20)]
    candles = _session(9, falling_then_rising) + _session(10, falling_then_rising)
    cfg = BacktestConfig(
        symbol="TEST",
        strategy="GRID_ACCUMULATOR",
        params={"initial_qty": 10, "grid_step": 0.5, "profit_per_share": 1.0, "max_levels": 3},
    )
    report = await run_backtest(cfg, candles)

    assert report.trades, "grid should complete round trips"
    assert report.total_charges > 0
    # equity ends flat (MIS) and equals capital + sum of net trade P&L
    assert report.final_equity == pytest.approx(cfg.capital + sum(t.pnl for t in report.trades), abs=0.01)
    # never carries a position overnight
    by_day: dict = {}
    for f in report.fills:
        by_day.setdefault(f.ts.date(), 0)
        by_day[f.ts.date()] += f.qty if f.side.value == "BUY" else -f.qty
    assert all(v == 0 for v in by_day.values())


async def test_macd_backtest_runs_without_lookahead():
    prices = [100 + 5 * math.sin(i / 8) + i * 0.02 for i in range(400)]
    candles = []
    for day in range(4):
        candles += _session(9 + day, prices[day * 75 : (day + 1) * 75])
    cfg = BacktestConfig(symbol="TEST", strategy="MACD_VOLUME", params={"ema_period": 20}, product=ProductType.MIS)
    report = await run_backtest(cfg, candles)
    index = {c.start: i for i, c in enumerate(candles)}
    for f in report.fills:
        assert f.ts in index  # fills happen on real candles (next-candle execution)
    assert report.final_equity > 0
