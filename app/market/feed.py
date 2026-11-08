"""
Live market data from the Kotak Neo SFeed WebSocket.

The SDK already reconnects and re-subscribes after drops. On top of that a watchdog
rebuilds the socket when subscribed instruments go silent during market hours
("zombie" connection) or when the SDK gives up reconnecting.
"""

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from typing import Any, Optional

from neo_api_client.websocket.feed import SFeedScrip, SFeedScripLite, SFeedWebSocket, WsToken

from app.broker.kotak_session import KotakSession
from app.core.clock import IST, is_market_open, now_ist
from app.domain.models import Tick
from app.market.bus import TickBus

logger = logging.getLogger("MarketFeed")

EXCHANGE_SEGMENT = "nse_cm"


def to_tick(message: Any, received_at: Optional[datetime] = None) -> Optional[Tick]:
    """Maps an SDK feed message to a Tick, or None for non-price messages."""
    if not isinstance(message, (SFeedScrip, SFeedScripLite)):
        return None
    if message.last_traded_price <= 0:
        return None
    ts = received_at or now_ist()
    if message.last_trade_time > 0:
        ts = datetime.fromtimestamp(message.last_trade_time, tz=IST)
    volume = getattr(message, "volume_traded_today", 0) or 0
    return Tick(
        token=str(message.instrument_token),
        ltp=float(message.last_traded_price),
        ts=ts,
        volume=int(volume),
        symbol=message.trading_symbol,
    )


class MarketFeed:
    def __init__(self, session: KotakSession, bus: TickBus, silence_threshold: float = 30.0):
        self._session = session
        self._bus = bus
        self.silence_threshold = silence_threshold
        self._tokens: set[str] = set()
        self._ws: Optional[SFeedWebSocket] = None
        self._task: Optional[asyncio.Task] = None
        self.last_message_at: float = 0.0
        self.connected = False

    # ------------------------------------------------------------------ public
    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="market-feed")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def subscribe(self, tokens: list[str]) -> None:
        new = {str(t) for t in tokens} - self._tokens
        if not new:
            return
        self._tokens |= new
        ws = self._ws
        if ws is not None and ws.is_connected:
            try:
                await ws.subscribe_scrips([WsToken(EXCHANGE_SEGMENT, t) for t in new])
                logger.info("📡 Subscribed %d tokens", len(new))
            except Exception as e:  # resubscribed on next reconnect
                logger.error("Subscribe failed: %s", e)

    async def unsubscribe(self, tokens: list[str]) -> None:
        gone = {str(t) for t in tokens} & self._tokens
        if not gone:
            return
        self._tokens -= gone
        ws = self._ws
        if ws is not None and ws.is_connected:
            with contextlib.suppress(Exception):
                await ws.unsubscribe_scrips([WsToken(EXCHANGE_SEGMENT, t) for t in gone])

    @property
    def seconds_since_data(self) -> float:
        return time.monotonic() - self.last_message_at if self.last_message_at else float("inf")

    # ------------------------------------------------------------------ internals
    async def _run(self) -> None:
        backoff = 2
        while True:
            ws: Optional[SFeedWebSocket] = None
            reader: Optional[asyncio.Task] = None
            try:
                await self._session.ensure_login()
                ws = self._session.client.create_websocket()
                await ws.connect()
                self._ws, self.connected = ws, True
                self.last_message_at = time.monotonic()
                logger.info("⚡ Market feed connected")
                if self._tokens:
                    await ws.subscribe_scrips([WsToken(EXCHANGE_SEGMENT, t) for t in self._tokens])
                reader = asyncio.create_task(self._read(ws))
                backoff = 2
                await self._watchdog(reader)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Feed error: %s (retry in %ss)", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                self._ws, self.connected = None, False
                if reader is not None:
                    reader.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await reader
                if ws is not None:
                    with contextlib.suppress(Exception):
                        await ws.close()

    async def _read(self, ws: SFeedWebSocket) -> None:
        async for message in ws:
            self.last_message_at = time.monotonic()
            tick = to_tick(message)
            if tick is not None:
                self._bus.publish(tick)

    async def _watchdog(self, reader: asyncio.Task) -> None:
        while True:
            await asyncio.sleep(1)
            if reader.done():
                raise ConnectionError(f"feed stream ended: {reader.exception() or 'reconnect exhausted'}")
            # Silence is only suspicious while the market is trading.
            if self._tokens and is_market_open(now_ist()) and self.seconds_since_data > self.silence_threshold:
                raise ConnectionError(f"no data for {self.seconds_since_data:.0f}s (zombie connection)")
