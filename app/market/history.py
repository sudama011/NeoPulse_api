"""Historical candles from Kotak Neo, used to warm up strategy indicators on (re)start."""

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from app.broker.kotak_session import KotakSession, response_error
from app.core.clock import IST, now_ist
from app.core.limiter import RateLimiter
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


class HistoryUnavailable(RuntimeError):
    """History could not be fetched (rate limit / outage). Callers should retry later."""


def _retry_after(resp: object, attempt: int) -> float:
    """Backoff for a rate-limited response, honouring Retry-After when the SDK surfaces it."""
    if isinstance(resp, dict):
        hint = (resp.get("rateLimit") or {}).get("Retry-After")
        try:
            return min(float(hint), 60.0)
        except (TypeError, ValueError):
            pass
    return min(2.0 * 2**attempt, 30.0)


def _is_rate_limited(resp: object, error: Optional[str]) -> bool:
    text = f"{error or ''} {resp if isinstance(resp, dict) else ''}".lower()
    return "429" in text or "rate limit" in text or "too many" in text


class HistoryService:
    """
    Kotak historical candles. The history endpoint is rate limited far more strictly than other
    REST calls, so requests go through their own throttle and back off on HTTP 429.
    """

    def __init__(self, session: KotakSession, calls_per_second: float = 1.0):
        self._session = session
        self._limiter = RateLimiter(calls_per_second=calls_per_second, burst_size=1)

    async def _fetch(self, retries: int, **params) -> dict:
        resp: object = None
        for attempt in range(retries + 1):
            await self._limiter.acquire()
            try:
                resp = await self._session.call(self._session.client.historical_data, **params)
            except Exception as e:
                raise HistoryUnavailable(str(e)) from e
            error = response_error(resp)
            ok = isinstance(resp, dict) and str(resp.get("status", "")).lower() in ("success", "ok")
            if ok and error is None:
                return resp  # type: ignore[return-value]
            if not _is_rate_limited(resp, error) or attempt == retries:
                raise HistoryUnavailable(error or str(resp))
            delay = _retry_after(resp, attempt)
            logger.warning("History rate limited for %s; retrying in %.0fs", params.get("neosymbol"), delay)
            await asyncio.sleep(delay)
        raise HistoryUnavailable(str(resp))

    async def range(
        self, token: str, timeframe_min: int, start: date, end: date, segment: str = "nse_cm"
    ) -> list[Candle]:
        """Closed candles between two dates, fetched in windows the backend allows. Raises on errors."""
        interval, max_days = _INTERVALS[timeframe_min]
        out: dict[datetime, Candle] = {}
        cursor = start
        while cursor <= end:
            window_end = min(end, cursor + timedelta(days=max_days - 1))
            resp = await self._fetch(
                5,
                neosymbol=f"{segment}|{token}",
                interval=interval,
                from_date=cursor.isoformat(),
                to_date=window_end.isoformat(),
            )
            for c in self._parse(resp, timeframe_min):
                out[c.start] = c
            cursor = window_end + timedelta(days=1)
        return [out[k] for k in sorted(out)]

    @staticmethod
    def _parse(resp: dict, timeframe_min: int) -> list[Candle]:
        now = now_ist()
        bucket = timedelta(minutes=timeframe_min)
        out: list[Candle] = []
        for row in (resp.get("data") or {}).get("candles") or []:
            try:
                start = datetime.fromisoformat(str(row[0])).astimezone(IST)
                if start + bucket > now:  # still forming
                    continue
                out.append(Candle(start, float(row[1]), float(row[2]), float(row[3]), float(row[4]), int(row[5] or 0)))
            except (ValueError, TypeError, IndexError):
                continue
        return out

    async def candles(
        self, token: str, timeframe_min: int, count: int, segment: str = "nse_cm", retries: int = 3
    ) -> list[Candle]:
        """Returns up to `count` fully closed candles, oldest first. Raises HistoryUnavailable on failure."""
        if timeframe_min not in _INTERVALS:
            raise ValueError(f"Unsupported timeframe {timeframe_min}m; use one of {sorted(_INTERVALS)}")
        interval, max_days = _INTERVALS[timeframe_min]

        # ~75 candles of 5m per day; fetch enough calendar days for weekends/holidays.
        per_day = max(1, 375 // timeframe_min)
        days = min(max_days, max(3, int(count / per_day * 1.6) + 3))
        now = now_ist()
        resp = await self._fetch(
            retries,
            neosymbol=f"{segment}|{token}",
            interval=interval,
            from_date=(now - timedelta(days=days)).strftime("%Y-%m-%d"),
            to_date=now.strftime("%Y-%m-%d"),
        )
        out = self._parse(resp, timeframe_min)
        out.sort(key=lambda c: c.start)
        return out[-count:]
