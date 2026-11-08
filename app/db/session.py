from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.DATABASE_URL, pool_size=10, max_overflow=10, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
