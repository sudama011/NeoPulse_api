from app.domain.models import Candle
from app.strategy import register_strategy
from app.strategy.base import Strategy
from app.strategy.indicators import tb


@register_strategy("DONCHIAN_TREND")
class DonchianTrendStrategy(Strategy):
    """Turtle-style breakout trend follower (long only).

    Entry: close > highest high of the previous `entry_period` bars and close > SMA(trend_period).
    Exit: close < lowest low of the previous `exit_period` bars, or initial stop at entry - stop_atr_mult * ATR.
    Params (defaults): entry_period=55, exit_period=20, trend_period=200, stop_atr_mult=2.0, atr_period=20.

    Research (Nov 2026, 40 Nifty-50 stocks, daily 2015-2026, CNC, 5 slots): CAGR +1.9%, Sharpe 0.2, MaxDD -35%;
    out-of-sample (2022+) negative. Kept for research; NOT recommended for live use.
    """

    def setup(self) -> None:
        p = self.params
        self.entry_period = int(p.get("entry_period", 55))
        self.exit_period = int(p.get("exit_period", 20))
        self.trend_period = int(p.get("trend_period", 200))
        self.stop_mult = float(p.get("stop_atr_mult", 2.0))
        self.atr_period = int(p.get("atr_period", 20))
        self.warmup = max(self.entry_period, self.trend_period) + 1

    async def on_candle(self, candle: Candle) -> None:
        if self.ctx.has_pending_order:
            return
        prior = list(self.candles)[:-1]
        if self.position > 0:
            if candle.close < min(c.low for c in prior[-self.exit_period :]):
                await self.exit("DONCHIAN_EXIT")
            return
        breakout = candle.close > max(c.high for c in prior[-self.entry_period :])
        if not breakout or candle.close <= tb.sma([c.close for c in self.candles], self.trend_period):
            return
        atr = tb.atr([c.to_dict() for c in self.candles][-(self.atr_period * 3) :], self.atr_period)
        if atr > 0:
            await self.buy(candle.close, stop=candle.close - self.stop_mult * atr, reason="DONCHIAN_BREAKOUT")
