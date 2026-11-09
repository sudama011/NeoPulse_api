"""Tick -> OHLCV candle aggregation for one instrument and timeframe."""

from datetime import datetime, timedelta
from typing import Optional

from app.core.clock import candle_start
from app.domain.models import Candle, Tick


class CandleBuilder:
    """
    Builds time-aligned candles. A candle is emitted when a tick of a later bucket arrives,
    or when `flush(now)` is called after the bucket has ended (so quiet instruments still
    close their candles on time).

    Volume uses the feed's cumulative day volume: candle volume = delta since previous tick.
    """

    def __init__(self, timeframe_min: int, close_grace_seconds: float = 2.0):
        self.timeframe = timeframe_min
        self._grace = timedelta(seconds=close_grace_seconds)
        self._current: Optional[Candle] = None
        self._last_cum_volume: Optional[int] = None
        self.last_closed_start: Optional[datetime] = None

    def seed(self, last_closed: Candle) -> None:
        """Marks history as already processed so it is never emitted twice (never moves backwards)."""
        if self.last_closed_start is None or last_closed.start > self.last_closed_start:
            self.last_closed_start = last_closed.start

    def update(self, tick: Tick) -> Optional[Candle]:
        start = candle_start(tick.ts, self.timeframe)
        vol = self._volume_delta(tick.volume)

        if self.last_closed_start is not None and start <= self.last_closed_start:
            return None  # late tick for an already-closed bucket

        closed = None
        if self._current is not None and start > self._current.start:
            closed = self._close()

        if self._current is None:
            self._current = Candle(start=start, open=tick.ltp, high=tick.ltp, low=tick.ltp, close=tick.ltp, volume=vol)
        else:
            c = self._current
            c.high = max(c.high, tick.ltp)
            c.low = min(c.low, tick.ltp)
            c.close = tick.ltp
            c.volume += vol
        return closed

    def flush(self, now: datetime) -> Optional[Candle]:
        """Closes the current candle if its bucket has ended."""
        c = self._current
        if c is not None and now >= c.start + timedelta(minutes=self.timeframe) + self._grace:
            return self._close()
        return None

    def _close(self) -> Candle:
        candle = self._current
        assert candle is not None
        self._current = None
        self.last_closed_start = candle.start
        return candle

    def _volume_delta(self, cumulative: int) -> int:
        if cumulative <= 0:
            return 0
        prev = self._last_cum_volume
        self._last_cum_volume = cumulative
        if prev is None or cumulative < prev:
            return 0
        return cumulative - prev
