import asyncio
import contextlib
import logging
import time
from typing import Any, Dict, Optional

from neo_api_client.websocket.feed import SFeedScrip, SFeedScripLite, SFeedWebSocket, WsToken

from app.core.constants import EXCHANGE_NSE
from app.data.stream import data_stream
from app.execution.kotak import kotak_adapter

logger = logging.getLogger("MarketFeed")


def normalize_message(message: Any) -> Optional[Dict[str, Any]]:
    """
    Converts a typed SDK v3 SFeed message into the legacy tick dict the rest of the
    system consumes (DataStream routes on "tk"; aggregator/strategies read "ltp"/"v").
    Returns None for messages that are not per-instrument price updates.

    Keys: tk (token), e (segment), ts (trading symbol), ltp, ltq, ltt, v (cumulative
    day volume), op/h/lo/c (day OHLC). Prices arrive pre-scaled by the SDK.
    """
    if isinstance(message, SFeedScrip):
        return {
            "tk": str(message.instrument_token),
            "e": message.exchange_segment,
            "ts": message.trading_symbol,
            "ltp": message.last_traded_price,
            "ltq": message.last_trade_qty,
            "ltt": message.last_trade_time,
            "v": message.volume_traded_today,
            "op": message.open_price,
            "h": message.high_price,
            "lo": message.low_price,
            "c": message.close_price,
        }
    if isinstance(message, SFeedScripLite):
        return {
            "tk": str(message.instrument_token),
            "e": message.exchange_segment,
            "ts": message.trading_symbol,
            "ltp": message.last_traded_price,
            "ltq": message.last_trade_qty,
            "ltt": message.last_trade_time,
            "c": message.close_price,
        }
    return None


class MarketFeed:
    """
    Self-Healing market feed on top of the Kotak Neo SDK v3 async SFeed WebSocket.

    - The SDK handles transport-level auto-reconnect + re-subscription.
    - We add a Zombie watchdog: if subscribed tokens go silent for `silence_threshold`
      seconds, or the SDK gives up reconnecting, the socket is torn down and rebuilt
      with exponential backoff.
    - Runs entirely on the main event loop (no SDK thread / thread bridge in v3).
    """

    def __init__(self):
        self.subscribed_tokens: set = set()
        self.reconnect_delay = 2

        # Watchdog State
        self.last_packet_time = time.time()
        self.silence_threshold = 10.0
        self._stop_event = asyncio.Event()

        self._ws: Optional[SFeedWebSocket] = None

    @staticmethod
    def _ws_tokens(tokens) -> list:
        return [WsToken(EXCHANGE_NSE, str(t)) for t in tokens]

    async def _read_loop(self, ws: SFeedWebSocket):
        """Pumps SDK messages into the DataStream. Ends when the SDK stops reconnecting."""
        async for message in ws:
            self.last_packet_time = time.time()
            tick = normalize_message(message)
            if tick is not None:
                await data_stream.publish([tick])

    async def connect(self):
        """
        Main Connection Loop.
        """
        logger.info("📡 Feed Manager Started.")

        while not self._stop_event.is_set():
            # The v3 WebSocket authenticates with the trade session from totp_validate().
            if not kotak_adapter.is_logged_in:
                logger.info("Waiting for Broker Login...")
                await asyncio.sleep(2)
                continue

            ws: Optional[SFeedWebSocket] = None
            reader: Optional[asyncio.Task] = None
            try:
                ws = kotak_adapter.client.create_websocket()
                await ws.connect()
                self._ws = ws
                logger.info("⚡ WebSocket Connection OPENED.")

                # Resubscribe
                self.last_packet_time = time.time()
                if self.subscribed_tokens:
                    await ws.subscribe_scrips(self._ws_tokens(self.subscribed_tokens))
                    logger.info(f"✅ Subscribed to {len(self.subscribed_tokens)} tokens")

                reader = asyncio.create_task(self._read_loop(ws))
                self.reconnect_delay = 2

                # Watchdog Loop
                while not self._stop_event.is_set():
                    if reader.done():
                        exc = reader.exception()
                        raise ConnectionError(f"Feed stream ended: {exc or 'SDK reconnect exhausted'}")

                    if not self.subscribed_tokens:
                        self.last_packet_time = time.time()
                    else:
                        silence = time.time() - self.last_packet_time
                        if silence > self.silence_threshold:
                            raise ConnectionError(f"Zombie Connection! Silence for {silence:.1f}s")

                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"⚠️ Feed Connection/Watchdog Error: {e}")
                logger.info(f"⏳ Retrying in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, 60)
            finally:
                self._ws = None
                if reader is not None and not reader.done():
                    reader.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await reader
                if ws is not None:
                    with contextlib.suppress(Exception):
                        await ws.close()
                    logger.warning("🔌 Feed Disconnected.")

    async def subscribe(self, tokens: list):
        """
        Registers tokens. If the socket is live they are subscribed immediately;
        otherwise they are sent on the next (re)connect.
        """
        if not tokens:
            return

        tokens = [str(t) for t in tokens]
        self.subscribed_tokens.update(tokens)

        ws = self._ws
        if ws is None or not ws.is_connected:
            logger.info(f"🕒 Queued {len(tokens)} tokens; will subscribe on connect")
            return

        try:
            await ws.subscribe_scrips(self._ws_tokens(tokens))
            logger.info(f"✅ Subscribed to {len(tokens)} tokens")
        except Exception as e:
            logger.error(f"❌ Subscribe Failed: {e}")


market_feed = MarketFeed()
