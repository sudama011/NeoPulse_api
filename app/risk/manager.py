"""
Account-level risk.

Rules apply to ENTRIES only. Exits (reducing exposure) are never blocked, otherwise a
risk breach could trap the account in a losing position.

Kill switch: tripped automatically when today's net P&L (realized - charges + unrealized)
breaches MAX_DAILY_LOSS, or manually via the API. When tripped, all positions are
flattened and new entries are refused for the rest of the day. The flag is persisted so a
restart cannot silently re-enable trading.
"""

import logging
import time
from datetime import time as dtime
from typing import Awaitable, Callable, Optional

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.broker.base import Broker
from app.core.clock import MARKET_OPEN, is_trading_day, now_ist, parse_hhmm, today_ist
from app.core.settings import Settings
from app.core.tasks import fire_and_forget
from app.models import KeyValue
from app.notifications.notifier import Notifier
from app.portfolio.book import DayPnl, PositionBook

logger = logging.getLogger("Risk")

KILL_SWITCH_KEY = "kill_switch"
PriceSource = Callable[[str], Optional[float]]


class RiskManager:
    def __init__(
        self,
        settings: Settings,
        positions: PositionBook,
        broker: Broker,
        prices: PriceSource,
        session_factory: async_sessionmaker,
        notifier: Notifier,
    ):
        self._settings = settings
        self._positions = positions
        self._broker = broker
        self._prices = prices
        self._sf = session_factory
        self._notifier = notifier
        self.max_daily_loss = settings.MAX_DAILY_LOSS
        self.max_open_positions = settings.MAX_OPEN_POSITIONS
        self._entry_cutoff: dtime = parse_hhmm(settings.ENTRY_CUTOFF)
        self.kill_switch = False
        self.kill_reason: Optional[str] = None
        self._margin_cache: tuple[float, Optional[float]] = (0.0, None)
        self._on_kill: Optional[Callable[[str], Awaitable[None]]] = None

    def on_kill(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """Registers the action to take when the kill switch trips (flatten everything)."""
        self._on_kill = callback

    async def load(self) -> None:
        async with self._sf() as db:
            row = await db.get(KeyValue, KILL_SWITCH_KEY)
        if row and isinstance(row.value, dict) and row.value.get("date") == today_ist().isoformat():
            self.kill_switch = bool(row.value.get("active"))
            self.kill_reason = row.value.get("reason")
            if self.kill_switch:
                logger.critical("💀 Kill switch is ACTIVE from earlier today: %s", self.kill_reason)

    # ------------------------------------------------------------------ checks
    def day_pnl(self) -> DayPnl:
        return self._positions.day_pnl(self._prices)

    async def check_entry(self, instance_id: str, qty: int, price: float, leverage: float = 1.0) -> Optional[str]:
        """Returns None if the entry is allowed, else a human-readable rejection reason."""
        if self.kill_switch:
            return f"kill switch active ({self.kill_reason})"

        now = now_ist()
        if not is_trading_day(now.date(), self._settings.holidays):
            return "not a trading day"
        if not (MARKET_OPEN <= now.time() < self._entry_cutoff):
            return f"outside entry window {MARKET_OPEN:%H:%M}-{self._entry_cutoff:%H:%M}"

        pnl = self.day_pnl()
        if pnl.net <= -self.max_daily_loss:
            await self.trip(f"daily loss {pnl.net:.2f} breached limit {self.max_daily_loss:.2f}")
            return "daily loss limit reached"

        open_positions = self._positions.open_positions()
        if instance_id not in open_positions and len(open_positions) >= self.max_open_positions:
            return f"max open positions ({self.max_open_positions}) reached"

        margin = await self._available_margin()
        required = qty * price / max(leverage, 1.0)
        if margin is not None and required > margin:
            return f"insufficient margin: need {required:.0f}, available {margin:.0f}"
        return None

    async def _available_margin(self) -> Optional[float]:
        fetched_at, value = self._margin_cache
        if time.monotonic() - fetched_at > 10:
            value = await self._broker.available_margin()
            self._margin_cache = (time.monotonic(), value)
        return value

    # ------------------------------------------------------------------ kill switch
    async def monitor(self) -> None:
        """Periodic job: trips the kill switch on a daily-loss breach (includes unrealized)."""
        if self.kill_switch:
            return
        pnl = self.day_pnl()
        if pnl.net <= -self.max_daily_loss:
            await self.trip(f"daily loss {pnl.net:.2f} breached limit {self.max_daily_loss:.2f}")

    async def trip(self, reason: str) -> None:
        if self.kill_switch:
            return
        self.kill_switch, self.kill_reason = True, reason
        await self._save(True, reason)
        logger.critical("💀 KILL SWITCH: %s", reason)
        self._notifier.send(f"KILL SWITCH ACTIVATED\n{reason}\nFlattening all positions.", level="CRITICAL")
        if self._on_kill:
            fire_and_forget(self._on_kill(reason))  # may be called from inside a strategy task

    async def reset(self) -> None:
        self.kill_switch, self.kill_reason = False, None
        await self._save(False, None)
        logger.warning("🟢 Kill switch reset manually")
        self._notifier.send("Kill switch reset. Entries are allowed again.", level="WARNING")

    async def _save(self, active: bool, reason: Optional[str]) -> None:
        value = {"active": active, "reason": reason, "date": today_ist().isoformat()}
        async with self._sf() as db, db.begin():
            stmt = insert(KeyValue).values(key=KILL_SWITCH_KEY, value=value)
            await db.execute(stmt.on_conflict_do_update(index_elements=[KeyValue.key], set_={"value": value}))
