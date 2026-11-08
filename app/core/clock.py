"""Market calendar and time helpers (IST)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def start_of_day(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=IST)


def parse_hhmm(value: str) -> time:
    hh, mm = value.strip().split(":")
    return time(int(hh), int(mm))


def is_trading_day(d: date, holidays: frozenset[date] = frozenset()) -> bool:
    return d.weekday() < 5 and d not in holidays


def is_market_open(ts: datetime, holidays: frozenset[date] = frozenset()) -> bool:
    ts = ts.astimezone(IST)
    return is_trading_day(ts.date(), holidays) and MARKET_OPEN <= ts.time() < MARKET_CLOSE


def candle_start(ts: datetime, timeframe_min: int) -> datetime:
    """Floors `ts` to the candle bucket, aligned to the 09:15 session open (NSE convention)."""
    ts = ts.astimezone(IST)
    session_open = datetime.combine(ts.date(), MARKET_OPEN, tzinfo=IST)
    if ts < session_open:
        minute = ts.replace(second=0, microsecond=0)
        return minute - timedelta(minutes=minute.minute % timeframe_min)
    elapsed = int((ts - session_open).total_seconds() // 60)
    return session_open + timedelta(minutes=(elapsed // timeframe_min) * timeframe_min)
