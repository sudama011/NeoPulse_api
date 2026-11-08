"""
Daily trading calendar.

Each job runs at most once per trading day, and only inside its [start, end) window, so a
restart in the middle of the day catches up on anything missed (e.g. square-off) without
ever running a job at the wrong time (e.g. sending exit orders after the market closed).
"""

import logging
from dataclasses import dataclass
from datetime import date, time
from typing import Awaitable, Callable

from app.core.clock import is_trading_day, now_ist

logger = logging.getLogger("Scheduler")


@dataclass
class DailyJob:
    name: str
    start: time
    end: time
    action: Callable[[], Awaitable[None]]
    trading_days_only: bool = True
    last_run: date | None = None


class Scheduler:
    def __init__(self, holidays: frozenset[date] = frozenset()):
        self._jobs: list[DailyJob] = []
        self._holidays = holidays

    def daily(
        self, name: str, start: time, end: time, action: Callable[[], Awaitable[None]], trading_days_only: bool = True
    ) -> None:
        self._jobs.append(DailyJob(name, start, end, action, trading_days_only))

    async def run_due(self) -> None:
        now = now_ist()
        today = now.date()
        for job in self._jobs:
            if job.last_run == today:
                continue
            if job.trading_days_only and not is_trading_day(today, self._holidays):
                continue
            if not (job.start <= now.time() < job.end):
                continue
            logger.info("⏰ Running job '%s'", job.name)
            try:
                await job.action()
                job.last_run = today
            except Exception:
                logger.exception("Job '%s' failed; will retry", job.name)

    def jobs(self) -> list[dict]:
        return [
            {"name": j.name, "window": f"{j.start:%H:%M}-{j.end:%H:%M}", "last_run": j.last_run} for j in self._jobs
        ]
