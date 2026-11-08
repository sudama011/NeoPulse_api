"""Historical candles from Kotak Neo, used to warm up strategy indicators on (re)start."""

import logging
from datetime import datetime, timedelta

from app.broker.kotak_session import KotakSession, response_error
from app.core.clock import IST, now_ist
from app.domain.models import Candle

logger = logging.getLogger("History")

# Kotak interval strings and the backend's max look-back for each.
_INTERVALS = {
    1: ("1min", 30),
    3: ("3min", 30),
    5: ("5min", 30),
    10: ("10min", 60),
    15: ("15min", 60),
    30: ("30min", 90),
    60: ("60min", 90),
}


def supported_timeframe(minutes: int) -> bool:
    return minutes in _INTERVALS


class HistoryService:
    def __init__(self, session: KotakSession):
        self._session = session

    async def candles(self, token: str, timeframe_min: int, count: int, segment: str = "nse_cm") -> list[Candle]:
        """Returns up to `count` fully closed candles, oldest first. Empty list on failure."""
        if timeframe_min not in _INTERVALS:
            raise ValueError(f"Unsupported timeframe {timeframe_min}m; use one of {sorted(_INTERVALS)}")
        interval, max_days = _INTERVALS[timeframe_min]

        # ~75 candles of 5m per day; fetch enough calendar days for weekends/holidays.
        per_day = max(1, 375 // timeframe_min)
        days = min(max_days, max(3, int(count / per_day * 1.6) + 3))
        now = now_ist()
        try:
            resp = await self._session.call(
                self._session.client.historical_data,
                neosymbol=f"{segment}|{token}",
                interval=interval,
                from_date=(now - timedelta(days=days)).strftime("%Y-%m-%d"),
                to_date=now.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            logger.error("History fetch failed for %s: %s", token, e)
            return []

        error = response_error(resp)
        if error or str(resp.get("status", "")).lower() not in ("success", "ok"):
            logger.error("History fetch failed for %s: %s", token, error or resp)
            return []

        out: list[Candle] = []
        bucket = timedelta(minutes=timeframe_min)
        for row in (resp.get("data") or {}).get("candles") or []:
            try:
                start = datetime.fromisoformat(str(row[0])).astimezone(IST)
                if start + bucket > now:  # still forming
                    continue
                out.append(Candle(start, float(row[1]), float(row[2]), float(row[3]), float(row[4]), int(row[5] or 0)))
            except (ValueError, TypeError, IndexError):
                continue
        out.sort(key=lambda c: c.start)
        return out[-count:]
