from app.domain.models import Candle
from app.strategy import register_strategy
from app.strategy.base import Strategy
from app.strategy.indicators import tb


@register_strategy("RSI2_PULLBACK")
class RSI2PullbackStrategy(Strategy):
    """Connors RSI(2) mean reversion: buy short-term dips inside a long-term uptrend (long only).

    Entry: close > SMA(trend_period) and RSI(rsi_period) < entry_rsi.
    Exit: close > SMA(exit_sma), or after max_hold_bars, or disaster stop at entry - stop_atr_mult * ATR.
    Params (defaults): trend_period=200, rsi_period=2, entry_rsi=10, exit_sma=5, max_hold_bars=10,
    stop_atr_mult=3.0, atr_period=14. Designed for daily candles (CNC).

    Research (Nov 2026, 40 Nifty-50 stocks, daily 2015-2026, CNC charges, 5 slots): CAGR -6%, Sharpe -0.3.
    Small gross edge, erased by delivery STT. Kept for research; NOT recommended for live use.
    """

    def setup(self) -> None:
        p = self.params
        self.trend_period = int(p.get("trend_period", 200))
        self.rsi_period = int(p.get("rsi_period", 2))
        self.entry_rsi = float(p.get("entry_rsi", 10))
        self.exit_sma = int(p.get("exit_sma", 5))
        self.max_hold = int(p.get("max_hold_bars", 10))
        self.stop_mult = float(p.get("stop_atr_mult", 3.0))
        self.atr_period = int(p.get("atr_period", 14))
        self.warmup = self.trend_period + 1

    async def on_candle(self, candle: Candle) -> None:
        if self.ctx.has_pending_order:
            return
        closes = [c.close for c in self.candles]

        if self.position > 0:
            held = int(self.state.get("bars_held", 0)) + 1
            self.state["bars_held"] = held
            if candle.close > tb.sma(closes, self.exit_sma) or held >= self.max_hold:
                await self.exit("RSI2_EXIT" if held < self.max_hold else "TIME_EXIT")
            return

        if candle.close <= tb.sma(closes, self.trend_period):
            return
        if tb.rsi(closes, self.rsi_period) >= self.entry_rsi:
            return
        atr = tb.atr([c.to_dict() for c in self.candles][-(self.atr_period * 3) :], self.atr_period)
        if atr <= 0:
            return
        if await self.buy(candle.close, stop=candle.close - self.stop_mult * atr, reason="RSI2_DIP"):
            self.state["bars_held"] = 0
