"""
Owns the Kotak Neo SDK client and its trade session.

Every Kotak component (orders, market feed, history, instrument sync) goes through
`KotakSession.call()`, which serialises blocking SDK calls onto the thread pool, rate
limits them, and transparently re-logs in once when the session has expired.
"""

import asyncio
import logging
from datetime import date
from typing import Any, Callable, Optional

import pyotp
from neo_api_client import NeoAPI

from app.core.clock import today_ist
from app.core.executors import run_blocking
from app.core.limiter import RateLimiter
from app.core.settings import Settings

logger = logging.getLogger("KotakSession")

_ERROR_KEYS = ("Error", "error", "Error Message")
_AUTH_HINTS = ("2fa", "session", "token", "unauthor", "401", "403", "login")


def response_error(resp: Any) -> Optional[str]:
    """Returns the error text when `resp` is an SDK/backend failure payload, else None."""
    if not isinstance(resp, dict):
        return f"unexpected response type {type(resp).__name__}"
    for key in _ERROR_KEYS:
        if key in resp:
            return str(resp[key])
    if str(resp.get("stat", "")).lower() == "not_ok":
        return str(resp.get("errMsg") or resp.get("emsg") or resp)
    return None


def is_auth_error(message: Optional[str]) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return any(h in lowered for h in _AUTH_HINTS)


class KotakSession:
    def __init__(self, settings: Settings, limiter: Optional[RateLimiter] = None):
        self._settings = settings
        self.client = NeoAPI(consumer_key=settings.NEO_CONSUMER_KEY, environment=settings.NEO_ENVIRONMENT)
        self._limiter = limiter or RateLimiter(calls_per_second=8, burst_size=10)
        self._login_lock = asyncio.Lock()
        self._session_day: Optional[date] = None

    @property
    def is_logged_in(self) -> bool:
        return self._session_day == today_ist()

    async def ensure_login(self, force: bool = False) -> None:
        """Logs in if there is no session for today (Kotak sessions are daily)."""
        async with self._login_lock:
            if self.is_logged_in and not force:
                return
            await run_blocking(self._login_sync)
            self._session_day = today_ist()
            logger.info("✅ Kotak session active for %s", self._settings.NEO_UCC)

    def _login_sync(self) -> None:
        s = self._settings
        resp = self.client.totp_login(mobile_number=s.NEO_MOBILE, ucc=s.NEO_UCC, totp=pyotp.TOTP(s.NEO_TOTP_SEED).now())
        if not isinstance(resp, dict) or not isinstance(resp.get("data"), dict):
            raise ConnectionError(f"TOTP login failed: {resp}")
        resp = self.client.totp_validate(mpin=s.NEO_MPIN)
        if not isinstance(resp, dict) or not isinstance(resp.get("data"), dict):
            raise ConnectionError(f"TOTP validate failed: {resp}")

    async def call(self, fn: Callable[..., Any], *args, retry_auth: bool = True, **kwargs) -> Any:
        """
        Runs a blocking SDK method. On an auth-looking error the session is refreshed and the
        call retried once. Returns the raw SDK response (callers inspect it with response_error).
        """
        await self.ensure_login()
        await self._limiter.acquire()
        resp = await run_blocking(fn, *args, **kwargs)

        if retry_auth and is_auth_error(response_error(resp)):
            logger.warning("🔑 Session looks expired (%s); re-logging in", response_error(resp))
            await self.ensure_login(force=True)
            await self._limiter.acquire()
            resp = await run_blocking(fn, *args, **kwargs)
        return resp
