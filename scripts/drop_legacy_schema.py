"""
One-off: removes tables left by the pre-v3 NeoPulse schema so `alembic upgrade head` can run.

Only the known legacy tables are touched, and only with --yes. Take a backup first
(`make db-backup`). The instrument master is re-downloaded by `make sync`.
"""

import argparse
import asyncio

from sqlalchemy import inspect, text

from app.core.settings import settings
from app.db.session import create_engine

LEGACY_TABLES = [
    "backtest_run", "instrument_master", "market_ticks", "order_ledger", "risk_profile",
    "system_config", "trade_log", "trading_session", "users",
]
# Present in both schemas; legacy only if it lacks the new columns.
SHARED_NAME = "strategy_instance"


async def main(confirm: bool) -> None:
    engine = create_engine(settings)
    try:
        async with engine.begin() as conn:
            insp_tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
            drop = [t for t in LEGACY_TABLES if t in insp_tables]
            if SHARED_NAME in insp_tables:
                cols = await conn.run_sync(lambda c: {col["name"] for col in inspect(c).get_columns(SHARED_NAME)})
                if "position_qty" not in cols:
                    drop.append(SHARED_NAME)
            revision = None
            if "alembic_version" in insp_tables:
                revision = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
                if revision and revision != "736ee925a41c":
                    drop.append("alembic_version")

            if not drop:
                print("No legacy tables found; nothing to do.")
                return
            print("Legacy tables to drop:", ", ".join(drop), f"(alembic revision {revision})")
            if not confirm:
                print("Dry run. Re-run with --yes to drop them.")
                return
            await conn.execute(text("DROP TABLE IF EXISTS " + ", ".join(f'"{t}"' for t in drop) + " CASCADE"))
            print("Dropped. Now run: make migrate")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true", help="actually drop the tables")
    asyncio.run(main(ap.parse_args().yes))
