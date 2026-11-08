"""Alerts. Fire-and-forget so a slow Telegram API can never block trading."""

import html
import logging
from typing import Optional

import httpx

from app.core.tasks import fire_and_forget

logger = logging.getLogger("Notifier")

_ICONS = {"INFO": "ℹ️", "SUCCESS": "✅", "WARNING": "⚠️", "ERROR": "❌", "CRITICAL": "🚨"}


class Notifier:
    def __init__(self, bot_token: Optional[str], chat_id: Optional[str], app_name: str = "NeoPulse"):
        self._chat_id = chat_id
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage" if bot_token else None
        self._app = app_name
        self._client: Optional[httpx.AsyncClient] = None
        if not (bot_token and chat_id):
            logger.warning("Telegram not configured; alerts are logged only")

    @property
    def enabled(self) -> bool:
        return bool(self._url and self._chat_id)

    def send(self, message: str, level: str = "INFO") -> None:
        log = logger.error if level in ("ERROR", "CRITICAL") else logger.info
        log("🔔 %s", message.replace("\n", " | "))
        if self.enabled:
            fire_and_forget(self._post(f"{_ICONS.get(level, '')} <b>{self._app}</b>\n{html.escape(message)}"))

    async def _post(self, text: str) -> None:
        if self._url is None:
            return
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=10.0)
            resp = await self._client.post(
                self._url, json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"}
            )
            if resp.status_code != 200:
                logger.warning("Telegram send failed: %s", resp.text[:200])
        except Exception as e:
            logger.warning("Telegram error: %s", e)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
