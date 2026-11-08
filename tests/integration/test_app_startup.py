"""The app must boot and serve the API even when the broker is unreachable."""

import asyncio
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import httpx

from app.api.routes import router
from app.container import Container
from app.core.settings import Settings
from tests.conftest import TEST_DATABASE_URL


def _test_settings() -> Settings:
    url = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql"))
    return Settings(
        POSTGRES_USER=url.username,
        POSTGRES_PASSWORD=url.password or "",
        POSTGRES_SERVER=url.hostname,
        POSTGRES_PORT=url.port,
        POSTGRES_DB=url.path.lstrip("/"),
        PAPER_TRADING=True,
        TELEGRAM_BOT_TOKEN="",
        TELEGRAM_CHAT_ID="",
    )


async def test_boots_without_broker_and_serves_api(sf):
    from fastapi import FastAPI

    container = Container(_test_settings())
    with patch.object(container.kotak, "ensure_login", AsyncMock(side_effect=ConnectionError("kotak down"))):
        await container.start()
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.state.container = container
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            health = (await client.get("/api/v1/health")).json()
            assert health["mode"] == "PAPER" and health["broker_session"] is False
            types = (await client.get("/api/v1/strategies/types")).json()
            assert "MACD_VOLUME" in types
            resp = await client.post(
                "/api/v1/strategies", json={"strategy_type": "MACD_VOLUME", "symbol": "NOPE", "capital": 1000}
            )
            assert resp.status_code == 400
            assert (await client.get("/api/v1/orders")).status_code == 200
        await asyncio.sleep(0)
        await container.stop()
