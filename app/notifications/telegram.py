"""Minimal async Telegram Bot API client (send, edit, long-poll updates, callback answers)."""

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger("TelegramClient")

LONG_POLL_SECONDS = 25


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str):
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(LONG_POLL_SECONDS + 10, connect=10))

    async def _call(self, method: str, **payload: Any) -> Any:
        resp = await self._http.post(f"{self._base}/{method}", json={k: v for k, v in payload.items() if v is not None})
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description', resp.text[:200])}")
        return data["result"]

    async def send_message(self, chat_id: str, text: str, reply_markup: Optional[dict] = None) -> int:
        result = await self._call(
            "sendMessage",
            chat_id=chat_id,
            text=text[:4096],
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return int(result["message_id"])

    async def edit_message(self, chat_id: str, message_id: int, text: str) -> None:
        try:
            await self._call(
                "editMessageText", chat_id=chat_id, message_id=message_id, text=text[:4096], parse_mode="HTML"
            )
        except TelegramError as e:  # e.g. "message is not modified"
            logger.debug("edit failed: %s", e)

    async def answer_callback(self, callback_id: str, text: Optional[str] = None) -> None:
        try:
            await self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)
        except TelegramError as e:
            logger.debug("answerCallbackQuery failed: %s", e)

    async def get_updates(self, offset: Optional[int], timeout: int = LONG_POLL_SECONDS) -> list[dict]:
        return await self._call(
            "getUpdates", offset=offset, timeout=timeout, allowed_updates=["message", "callback_query"]
        )

    async def set_commands(self, commands: list[tuple[str, str]]) -> None:
        await self._call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])

    async def close(self) -> None:
        await self._http.aclose()
