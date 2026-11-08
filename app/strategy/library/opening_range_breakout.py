from datetime import date
from typing import Optional

from app.core.clock import MARKET_OPEN
from app.domain.models import Candle
from app.strategy import register_strategy
from app.strategy.base import Strategy


@register_strategy("ORB")
class OpeningRangeBreakoutStrategy(Strategy):
    """Opening Range Breakout (intraday, long + short), with a relative-volume filter.

    The first `range_bars` candles of the session define the range. The first close beyond the range
    (in the direction of the opening candle, when `follow_open_direction`) enters; stop at the other side of
    the range (capped at `max_stop_pct` of price); target = `reward_risk` x risk; flat at square-off (MIS).
    Only trades when opening volume >= `min_rvol` x the average opening volume of the last 14 sessions
    ("stocks in play"). One trade per symbol per day.
    Params (defaults): range_bars=1, reward_risk=2.0, min_rvol=1.5, max_stop_pct=0.02,
    follow_open_direction=true, allow_short=true, last_entry_bar=6.
    """

    warmup = 0

    def setup(self) -> None:
        p = self.params
        self.range_bars = int(p.get("range_bars", 1))
        self.reward_risk = float(p.get("reward_risk", 2.0))
        self.min_rvol = float(p.get("min_rvol", 1.5))
        self.max_stop_pct = float(p.get("max_stop_pct", 0.02))
        self.follow_open = bool(p.get("follow_open_direction", True))
        self.allow_short = bool(p.get("allow_short", True))
        self.last_entry_bar = int(p.get("last_entry_bar", 6))
        self._day: Optional[date] = None
        self._bars: list[Candle] = []
        # Replay ~20 sessions on restart so the relative-volume baseline is ready immediately.
        self.history_bars = 20 * max(1, 375 // max(1, self.ctx.timeframe))

    async def on_candle(self, candle: Candle) -> None:
        day = candle.start.date()
        if day != self._day:
            self._day, self._bars = day, []
            if candle.start.time() != MARKET_OPEN:
                self._bars = [candle, candle]  # missed the open: no valid range today
                self.state["traded"] = True
            else:
                self.state["traded"] = False
        self._bars.append(candle)
        n = len(self._bars)

        if n == self.range_bars:
            self._record_opening_volume(sum(c.volume for c in self._bars))
            return
        if n <= self.range_bars or self.state.get("traded") or self.position or self.ctx.has_pending_order:
            return
        if n > self.range_bars + self.last_entry_bar or not self._in_play():
            return

        opening = self._bars[: self.range_bars]
        hi, lo = max(c.high for c in opening), min(c.low for c in opening)
        bullish_open = opening[-1].close >= opening[0].open
        price = candle.close
        max_risk = price * self.max_stop_pct

        if price > hi and (bullish_open or not self.follow_open):
            stop = max(lo, price - max_risk)
            if await self.buy(price, stop=stop, target=price + (price - stop) * self.reward_risk, reason="ORB_LONG"):
                self.state["traded"] = True
        elif price < lo and self.allow_short and (not bullish_open or not self.follow_open):
            stop = min(hi, price + max_risk)
            if await self.sell(price, stop=stop, target=price - (stop - price) * self.reward_risk, reason="ORB_SHORT"):
                self.state["traded"] = True

    def _record_opening_volume(self, volume: float) -> None:
        history = list(self.state.get("opening_volumes", []))[-14:]
        self.state["rvol"] = volume / (sum(history) / len(history)) if len(history) >= 5 and sum(history) else 0.0
        self.state["opening_volumes"] = history + [volume]

    def _in_play(self) -> bool:
        return self.state.get("rvol", 0.0) >= self.min_rvol
