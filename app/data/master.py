import asyncio
import logging
import pandas as pd
import io
import httpx
from datetime import datetime
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from app.db.session import AsyncSessionLocal
from app.models.market_data import InstrumentMaster
from app.execution.kotak import kotak_adapter
from app.core.constants import EXCHANGE_NSE

logger = logging.getLogger("MasterData")


class MasterDataManager:
    """
    Manages Instrument Tokens.
    1. Syncs Daily Script Master from Broker via URL.
    2. Provides In-Memory Lookup.
    """

    def __init__(self):
        self._symbol_to_token = {}
        self._token_to_symbol = {}
        self.is_loaded = False

    async def initialize(self):
        """Loads data from DB into Memory on startup."""
        if not self.is_loaded:
            await self._load_cache()
            logger.info(
                f"📚 Master Data Loaded: {len(self._symbol_to_token)} instruments in memory."
            )

    def get_token(self, symbol: str) -> str:
        return self._symbol_to_token.get(symbol)

    def get_symbol(self, token: str) -> str:
        return self._token_to_symbol.get(str(token))

    async def _load_cache(self):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(InstrumentMaster))
            instruments = result.scalars().all()

            self._symbol_to_token.clear()
            self._token_to_symbol.clear()

            for i in instruments:
                self._symbol_to_token[i.trading_symbol] = str(i.instrument_token)
                self._token_to_symbol[str(i.instrument_token)] = i.trading_symbol

            self.is_loaded = True

    async def sync_daily_script(self):
        """
        1. Gets URL from Kotak SDK.
        2. Downloads CSV via HTTPX.
        3. Parses & Inserts via Pandas + SQLAlchemy.
        """
        logger.info("🌍 Starting Scrip Master Sync...")

        try:
            # 1. Login & Get URLs (Blocking Call -> Thread)
            await kotak_adapter.login()

            logger.info("📡 Fetching Master URL from Kotak...")
            csv_url = await asyncio.to_thread(
                kotak_adapter.client.scrip_master, exchange_segment=EXCHANGE_NSE
            )

            logger.info(f"⬇️ Downloading CSV from: {csv_url}")

            # 3. Download Content Async (High Performance)
            async with httpx.AsyncClient() as client:
                resp = await client.get(csv_url, timeout=30.0)
                resp.raise_for_status()
                csv_content = resp.content  # Bytes

        except Exception as e:
            logger.critical(f"❌ Download Failed: {e}")
            return

        # 4. Parse with Pandas
        try:
            logger.info("🔄 Parsing CSV Data...")

            # Read from bytes directly
            df = pd.read_csv(io.BytesIO(csv_content))

            # Rename Columns (Map Kotak headers to DB schema)
            # Kotak Headers: pScripCode, pTrdSymbol, pSymbol, pLotSize, pTickSize, pExch
            df = df.rename(
                columns={
                    "pScripCode": "instrument_token",
                    "pTrdSymbol": "trading_symbol",
                    "pSymbol": "name",
                    "pLotSize": "lot_size",
                    "pTickSize": "tick_size",
                    "pExch": "segment",
                }
            )

            # Cleanup & Transform
            df["exchange_token"] = df["name"]
            df["updated_at"] = datetime.now()

            # Numeric conversion
            df["instrument_token"] = pd.to_numeric(
                df["instrument_token"], errors="coerce"
            )
            df["lot_size"] = pd.to_numeric(df["lot_size"], errors="coerce").fillna(1)
            df["tick_size"] = pd.to_numeric(df["tick_size"], errors="coerce").fillna(
                0.05
            )

            df = df.dropna(subset=["instrument_token", "trading_symbol"])

            data_to_insert = df.to_dict(orient="records")
            logger.info(f"📊 Parsed {len(data_to_insert)} records.")

        except Exception as e:
            logger.error(f"❌ Parsing Error: {e}")
            return

        # 5. Bulk Insert
        if data_to_insert:
            async with AsyncSessionLocal() as session:
                try:
                    logger.info("💾 Writing to Database...")
                    await session.execute(
                        text("TRUNCATE TABLE instrument_master CASCADE;")
                    )

                    # Batch Insert (5000 rows per batch)
                    chunk_size = 5000
                    for i in range(0, len(data_to_insert), chunk_size):
                        chunk = data_to_insert[i : i + chunk_size]
                        await session.execute(insert(InstrumentMaster), chunk)

                    await session.commit()
                    logger.info(
                        f"✅ Successfully inserted {len(data_to_insert)} symbols."
                    )
                except Exception as e:
                    await session.rollback()
                    logger.error(f"❌ Database Error: {e}")
                    return

        # 6. Reload Cache
        await self._load_cache()


master_data = MasterDataManager()
