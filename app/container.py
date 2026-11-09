"""
Composition root: builds every component with explicit dependencies and owns the
startup / shutdown sequence.

Startup is a recovery procedure, safe to run at any time of day:
  1. DB + instruments + persisted positions/P&L + kill switch flag
  2. Broker session (retried in the background if Kotak is unreachable)
  3. OMS recovery: unfinished orders matched against the broker order book (by tag)
  4. Position reconciliation against broker positions (live only)
  5. Market feed, strategy runners (warmed up from history), scheduler, monitors
"""

import asyncio
import html
import logging
from datetime import time, timedelta
from typing import Optional

from app.broker.base import Broker
from app.broker.kotak import KotakBroker
from app.broker.kotak_session import KotakSession
from app.broker.paper import PaperBroker
from app.control.telegram_bot import TelegramBot
from app.core.clock import MARKET_CLOSE, is_market_open, now_ist, parse_hhmm
from app.core.executors import global_executor
from app.core.settings import Settings
from app.core.tasks import TaskSupervisor
from app.db.session import create_engine, create_session_factory
from app.domain.models import Fill, OrderStatus, Side
from app.market.bus import TickBus
from app.market.feed import MarketFeed
from app.market.history import HistoryService
from app.market.instruments import InstrumentStore
from app.notifications.notifier import Notifier
from app.notifications.telegram import TelegramClient
from app.oms.order_manager import OrderManager, OrderView
from app.oms.reconciler import PositionReconciler
from app.portfolio.book import PositionBook
from app.portfolio.charges import ChargesCalculator
from app.risk.manager import RiskManager
from app.scheduler import Scheduler
from app.strategy.engine import StrategyEngine

logger = logging.getLogger("Container")


class Container:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_engine = create_engine(settings)
        self.sf = create_session_factory(self.db_engine)
        self.telegram = TelegramClient(settings.TELEGRAM_BOT_TOKEN) if settings.TELEGRAM_BOT_TOKEN else None
        self.notifier = Notifier(self.telegram, settings.TELEGRAM_CHAT_ID, settings.APP_NAME)

        self.kotak = KotakSession(settings)
        self.bus = TickBus()
        self.feed = MarketFeed(self.kotak, self.bus)
        self.history = HistoryService(self.kotak)
        self.instruments = InstrumentStore(self.sf)

        self.broker: Broker = (
            PaperBroker(self.bus.last_price, settings.PAPER_SLIPPAGE_BPS)
            if settings.PAPER_TRADING
            else KotakBroker(self.kotak)
        )
        self.positions = PositionBook(self.sf, ChargesCalculator())
        self.oms = OrderManager(self.broker, self.sf, self.positions, settings.LIMIT_ORDER_TTL_SECONDS)
        self.risk = RiskManager(settings, self.positions, self.broker, self.bus.last_price, self.sf, self.notifier)
        self.reconciler = (
            None if self.broker.is_paper else PositionReconciler(self.broker, self.positions, self.oms, self.notifier)
        )
        self.engine = StrategyEngine(
            self.sf,
            self.oms,
            self.positions,
            self.risk,
            self.bus,
            self.history,
            self.instruments,
            self.feed,
            self.notifier,
        )
        self.risk.on_kill(self.engine.flatten_all)
        self.oms.add_observer(self._on_order_event)
        self.bot = (
            TelegramBot(self.telegram, settings.TELEGRAM_CHAT_ID, self)
            if self.telegram and settings.TELEGRAM_CHAT_ID and settings.TELEGRAM_COMMANDS
            else None
        )
        self._alerted: set[str] = set()
        self.scheduler = Scheduler(settings.holidays)
        self.tasks = TaskSupervisor()
        self.started = False

    @property
    def mode(self) -> str:
        return "PAPER" if self.broker.is_paper else "LIVE"

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        global_executor.start()
        logger.info("🚀 Starting NeoPulse in %s mode", self.mode)

        await self.instruments.load()
        await self.positions.load()
        await self.risk.load()

        if not await self._connect_broker():
            self.tasks.spawn("broker-connect", self._connect_broker_forever())
        else:
            await self._recover()

        self._schedule_jobs()
        self.tasks.periodic("health-alerts", 30.0, self._health_alerts)
        if self.bot:
            self.tasks.periodic("telegram-bot", 5.0, self.bot.run)
        self.started = True
        ip = getattr(self.broker, "public_ip", None)
        self.notifier.send(
            f"NeoPulse started ({self.mode})" + (f"\nOrders go out from IP {ip} (must be whitelisted)" if ip else ""),
            level="SUCCESS",
        )

    async def stop(self) -> None:
        logger.info("🛑 Shutting down (positions are left as-is and resumed on restart)")
        await self.tasks.shutdown()
        await self.engine.shutdown()
        await self.oms.close()
        await self.feed.stop()
        await self.broker.stop()
        if self.telegram:
            await self.telegram.close()
        await self.db_engine.dispose()
        global_executor.stop()

    async def _connect_broker(self) -> bool:
        try:
            await self.kotak.ensure_login()
            await self.broker.start()
            return True
        except Exception as e:
            logger.error("Broker connection failed: %s", e)
            self.notifier.send(f"Broker login failed: {e}. Retrying in background.", level="ERROR")
            return False

    async def _connect_broker_forever(self) -> None:
        delay = 10
        while not await self._connect_broker():
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)
        await self._recover()

    async def _recover(self) -> None:
        if self.instruments.count == 0:
            await self._sync_instruments()
        await self.oms.recover()
        await self.engine.register_active()
        if self.reconciler:
            await self.reconciler.reconcile(startup=True)
        await self.feed.start()
        await self.engine.start_all()
        self._start_loops()

    def _start_loops(self) -> None:
        s = self.settings
        self.tasks.periodic("timers", 1.0, self.engine.tick_timers)
        self.tasks.periodic(
            "order-sync", lambda: s.ORDER_POLL_SECONDS if self.oms.has_open_orders else 15.0, self.oms.sync_with_broker
        )
        self.tasks.periodic("risk-monitor", 5.0, self.risk.monitor)
        self.tasks.periodic("supervisor", 30.0, self.engine.start_all)
        self.tasks.periodic("scheduler", 15.0, self.scheduler.run_due)
        if self.reconciler:
            self.tasks.periodic("reconcile", 60.0, self.reconciler.reconcile)

    def _schedule_jobs(self) -> None:
        s = self.settings
        square_off = parse_hhmm(s.SQUARE_OFF_TIME)
        self.scheduler.daily("session-refresh", time(8, 30), MARKET_CLOSE, self._refresh_session)
        self.scheduler.daily("instrument-sync", time(8, 35), MARKET_CLOSE, self._sync_instruments_if_stale)
        self.scheduler.daily("square-off", square_off, MARKET_CLOSE, self.engine.square_off_intraday)
        self.scheduler.daily("eod-report", time(15, 35), time(23, 59), self._eod_report)

    # ------------------------------------------------------------------ alerts
    async def _on_order_event(self, order: OrderView, fill: Optional[Fill]) -> None:
        tag = f"[{order.reason}]" if order.reason else ""
        if fill is not None:
            pos = self.positions.get(order.instance_id)
            pnl = f" · realized {fill.realized_pnl:+,.2f}" if fill.realized_pnl else ""
            self.notifier.send_html(
                f"{'🟢' if fill.side is Side.BUY else '🔴'} <b>{fill.side.value} {fill.qty} {html.escape(order.symbol)}"
                f" @ {fill.price:.2f}</b> {html.escape(tag)}\nPosition {pos.qty:+d}{pnl}"
            )
        elif order.status in (OrderStatus.REJECTED, OrderStatus.FAILED):
            self.notifier.send(
                f"Order {order.status.value}: {order.side.value} {order.qty} {order.symbol} {tag}\n{order.message}",
                level="ERROR",
            )

    async def _health_alerts(self) -> None:
        """One alert per incident (re-armed when the condition clears)."""
        if not is_market_open(now_ist(), self.settings.holidays) or not self.started:
            self._alerted.clear()
            return
        checks = {
            "feed": (
                bool(self.engine.runners) and self.feed.seconds_since_data > 120,
                "Market data feed silent for over 2 minutes",
            ),
            "session": (not self.kotak.is_logged_in, "Kotak session is not logged in"),
        }
        for key, (bad, message) in checks.items():
            if bad and key not in self._alerted:
                self._alerted.add(key)
                self.notifier.send(message, level="ERROR")
            elif not bad and key in self._alerted:
                self._alerted.discard(key)
                self.notifier.send(f"Recovered: {message.lower()}", level="SUCCESS")

    # ------------------------------------------------------------------ jobs
    async def _refresh_session(self) -> None:
        await self.kotak.ensure_login()

    async def _sync_instruments(self) -> None:
        try:
            await self.instruments.sync_from_broker(self.kotak)
        except Exception as e:
            self.notifier.send(f"Instrument sync failed: {e}", level="ERROR")
            raise

    async def _sync_instruments_if_stale(self) -> None:
        last = await self.instruments.last_synced()
        if last is None or now_ist() - last > timedelta(hours=12):
            await self._sync_instruments()

    async def _eod_report(self) -> None:
        pnl = self.risk.day_pnl()
        self.notifier.send(
            f"End of day\nRealized: {pnl.realized:.2f}\nCharges: {pnl.charges:.2f}\n"
            f"Unrealized: {pnl.unrealized:.2f}\nNet: {pnl.net:.2f}",
            level="INFO",
        )
