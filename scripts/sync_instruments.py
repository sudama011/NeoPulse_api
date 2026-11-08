"""Downloads today's Kotak scrip master into the database (also runs daily inside the app)."""

import asyncio

from app.broker.kotak_session import KotakSession
from app.core.executors import global_executor
from app.core.logger import setup_logging
from app.core.settings import settings
from app.db.session import create_engine, create_session_factory
from app.market.instruments import InstrumentStore


async def main() -> None:
    setup_logging()
    global_executor.start()
    engine = create_engine(settings)
    try:
        store = InstrumentStore(create_session_factory(engine))
        count = await store.sync_from_broker(KotakSession(settings))
        print(f"Synced {count} instruments")
    finally:
        await engine.dispose()
        global_executor.stop()


if __name__ == "__main__":
    asyncio.run(main())
