import os

# Required settings get harmless defaults so tests never need real credentials.
for key, value in {
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test",
    "NEO_CONSUMER_KEY": "test",
    "NEO_UCC": "test",
    "NEO_MOBILE": "+910000000000",
    "NEO_MPIN": "0000",
    "NEO_TOTP_SEED": "JBSWY3DPEHPK3PXP",
}.items():
    os.environ.setdefault(key, value)

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.models import Base  # noqa: E402

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://neopulse_test@localhost:55432/neopulse_test"
)


@pytest.fixture
async def sf():
    """Fresh schema per test on a real Postgres. Skips when no test database is reachable."""
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"test database unavailable: {e}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
