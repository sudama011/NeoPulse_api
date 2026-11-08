from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.clock import IST
from app.domain.models import Candle, ProductType, Side
from app.market.history import HistoryService
from backtest.engine import BacktestConfig, run_backtest


def _day(d: date, open_volume: int, breakout: bool) -> list[Candle]:
    """15m session: first bar 100->101; then breakout above 101.5 if `breakout`, else drift inside range."""
    t = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
    bars = [Candle(t, 100, 101.5, 99.5, 101, open_volume)]
    price = 101.0
    for i in range(1, 25):
        price = price + 0.4 if breakout and i < 8 else price
        bars.append(Candle(t + timedelta(minutes=15 * i), price, price + 0.2, price - 0.2, price, 1000))
    return bars


async def test_orb_trades_only_stocks_in_play_once_per_day_and_squares_off():
    candles: list[Candle] = []
    start = date(2026, 10, 1)
    for i in range(8):  # quiet baseline days, breakouts but no volume surge
        candles += _day(start + timedelta(days=i), 1000, breakout=True)
    candles += _day(start + timedelta(days=8), 5000, breakout=True)  # in play
    candles += _day(start + timedelta(days=9), 5000, breakout=False)  # in play, no breakout

    cfg = BacktestConfig(
        symbol="X",
        strategy="ORB",
        product=ProductType.MIS,
        timeframe=15,
        risk_per_trade_pct=1.0,
        params={"reward_risk": 10},
    )
    report = await run_backtest(cfg, candles)

    days_traded = {f.ts.date() for f in report.fills}
    assert days_traded == {start + timedelta(days=8)}
    entry, exit_ = report.fills
    assert entry.side is Side.BUY and exit_.side is Side.SELL
    assert exit_.ts.time() <= datetime(2026, 1, 1, 15, 15).time()  # squared off by 15:15
    assert report.trades[0].pnl > 0


async def test_history_range_fetches_in_backend_windows():
    calls = []

    async def call(fn, **kw):
        calls.append((kw["from_date"], kw["to_date"]))
        return {"status": "success", "data": {"candles": [[f"{kw['from_date']}T09:15:00+0530", 1, 2, 0.5, 1.5, 10]]}}

    session = SimpleNamespace(client=SimpleNamespace(historical_data=None), call=AsyncMock(side_effect=call))
    out = await HistoryService(session).range("1", 5, date(2026, 1, 1), date(2026, 3, 15))
    assert calls[0] == ("2026-01-01", "2026-01-30") and calls[-1][1] == "2026-03-15"
    assert all((date.fromisoformat(b) - date.fromisoformat(a)).days < 30 for a, b in calls)
    assert len(out) == len(calls) and out == sorted(out, key=lambda c: c.start)
