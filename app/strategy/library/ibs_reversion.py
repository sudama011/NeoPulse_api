from app.domain.models import Candle
from app.strategy import register_strategy
from app.strategy.base import Strategy
from app.strategy.indicators import tb


@register_strategy("IBS_REVERSION")
class IBSReversionStrategy(Strategy):
    """Internal Bar Strength mean reversion (long only); classically strongest on index ETFs.

    IBS = (close - low) / (high - low). Entry: IBS < entry_ibs and close > SMA(trend_period).
    Exit: IBS > exit_ibs, or after max_hold_bars, or disaster stop at entry - stop_atr_mult * ATR.
    Params (defaults): entry_ibs=0.2, exit_ibs=0.8, trend_period=200, max_hold_bars=5, stop_atr_mult=3.0.

    Research (Nov 2026, daily 2015-2026, CNC): Nifty-50 basket CAGR -0.5%; NIFTYBEES/BANKBEES negative even
    before charges. Kept for research; NOT recommended for live use.
    """

    def setup(self) -> None:
        p = self.params
        self.entry_ibs = float(p.get("entry_ibs", 0.2))
        self.exit_ibs = float(p.get("exit_ibs", 0.8))
        self.trend_period = int(p.get("trend_period", 200))
        self.max_hold = int(p.get("max_hold_bars", 5))
        self.stop_mult = float(p.get("stop_atr_mult", 3.0))
        self.warmup = self.trend_period + 1

    @staticmethod
    def ibs(c: Candle) -> float:
        rng = c.high - c.low
        return (c.close - c.low) / rng if rng > 0 else 0.5

    async def on_candle(self, candle: Candle) -> None:
        if self.ctx.has_pending_order:
            return
        if self.position > 0:
            held = int(self.state.get("bars_held", 0)) + 1
            self.state["bars_held"] = held
            if self.ibs(candle) > self.exit_ibs or held >= self.max_hold:
                await self.exit("IBS_EXIT")
            return
        if self.ibs(candle) >= self.entry_ibs:
            return
        if candle.close <= tb.sma([c.close for c in self.candles], self.trend_period):
            return
        atr = tb.atr([c.to_dict() for c in self.candles][-42:], 14)
        if atr > 0 and await self.buy(candle.close, stop=candle.close - self.stop_mult * atr, reason="IBS_LOW"):
            self.state["bars_held"] = 0
