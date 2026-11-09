"""Alerts. Fire-and-forget so a slow Telegram API can never block trading."""

import html
import logging
from typing import Optional

from app.core.tasks import fire_and_forget
from app.notifications.telegram import TelegramClient

logger = logging.getLogger("Notifier")

_ICONS = {"INFO": "ℹ️", "SUCCESS": "✅", "WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨"}


class Notifier:
    def __init__(self, client: Optional[TelegramClient], chat_id: Optional[str], app_name: str = "NeoPulse"):
        self._client = client
        self._chat_id = chat_id
        self._app = app_name
        if not self.enabled:
            logger.warning("Telegram not configured; alerts are logged only")

    @property
    def enabled(self) -> bool:
        return bool(self._client and self._chat_id)

    def send(self, message: str, level: str = "INFO") -> None:
        """Plain-text alert (escaped)."""
        log = logger.error if level in ("ERROR", "CRITICAL") else logger.info
        log("🔔 %s", message.replace("\n", " | "))
        self.send_html(f"{_ICONS.get(level, '')} <b>{self._app}</b>\n{html.escape(message)}")

    def send_html(self, text: str) -> None:
        if self.enabled:
            fire_and_forget(self._post(text))

    async def _post(self, text: str) -> None:
        assert self._client is not None and self._chat_id is not None
        try:
            await self._client.send_message(self._chat_id, text)
        except Exception as e:
            logger.warning("Telegram send failed: %r", e)
