import asyncio
import io
import logging
from datetime import datetime

import httpx
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.constants import EXCHANGE_NSE
from app.core.limiter import kotak_limiter
from app.db.session import AsyncSessionLocal
from app.execution.kotak import kotak_adapter
from app.models.market_data import InstrumentMaster

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
            logger.info(f"📚 Master Data Loaded: {len(self._symbol_to_token)} instruments in memory.")

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
        3. Parses & Inserts via Pandas with Precision Correction.
        """
        logger.info("🌍 Starting Scrip Master Sync...")

        try:
            # 1. Login & Get URLs (Blocking Call -> Thread)
            await kotak_adapter.login()

            logger.info("📡 Fetching Master URL from Kotak...")
            csv_url = await asyncio.to_thread(kotak_adapter.client.scrip_master, exchange_segment=EXCHANGE_NSE)

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

            # --- A. PRECISION CORRECTION ---
            # Kotak prices (integers) must be divided by 10^lPrecision
            df["lPrecision"] = pd.to_numeric(df["lPrecision"], errors="coerce").fillna(2)
            df["divider"] = 10 ** df["lPrecision"]

            # Columns that need division
            price_cols = ["dHighPriceRange", "dLowPriceRange", "dTickSize", "dStrikePrice"]

            for col in price_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
                    df[col] = df[col] / df["divider"]

            # --- B. DATE PARSING ---
            # lExpiryDate is usually an epoch integer or -1
            if "lExpiryDate" in df.columns:
                df["lExpiryDate"] = pd.to_numeric(df["lExpiryDate"], errors="coerce")
                # Convert strictly positive timestamps, else None
                df["lExpiryDate"] = df["lExpiryDate"].apply(
                    lambda x: datetime.fromtimestamp(x).date() if x > 0 else None
                )

            # --- C. RENAME COLUMNS (MAPPING) ---
            df = df.rename(
                columns={
                    # Identifiers
                    "pSymbol": "instrument_token",  # PK
                    "pTrdSymbol": "trading_symbol",
                    "pSymbolName": "symbol",  # NEW: Search Symbol
                    "pDesc": "name",
                    "pISIN": "isin",
                    # Segment & Type
                    "pExchSeg": "segment",  # nse_cm
                    "pExchange": "exchange",  # NSE
                    "pGroup": "series",  # EQ, BE
                    "pInstType": "instrument_type",
                    "pOptionType": "option_type",  # CE/PE
                    # Trading Specs
                    "lLotSize": "lot_size",
                    "dTickSize": "tick_size",
                    "lFreezeQty": "freeze_qty",
                    # Price Bands (Renamed per your Model)
                    "dHighPriceRange": "upper_band",  # Updated
                    "dLowPriceRange": "lower_band",  # Updated
                    # Derivatives / Extra
                    "lExpiryDate": "expiry_date",
                    "dStrikePrice": "strike_price",
                }
            )

            # --- D. DEFAULTS & CLEANUP ---
            df["updated_at"] = pd.Timestamp.now(tz="UTC")

            # Ensure exchange is set (default NSE if missing)
            if "exchange" not in df.columns:
                df["exchange"] = "NSE"
            else:
                df["exchange"] = df["exchange"].fillna("NSE")

            # Numeric Safety
            df["instrument_token"] = pd.to_numeric(df["instrument_token"], errors="coerce")
            df["lot_size"] = pd.to_numeric(df["lot_size"], errors="coerce").fillna(1)

            # Drop invalid rows (Must have a Token and Symbol)
            df = df.dropna(subset=["instrument_token", "trading_symbol"])

            # --- E. FILTER VALID COLUMNS ONLY ---
            # This prevents "column not found" errors in SQLAlchemy
            valid_cols = [
                "instrument_token",
                "trading_symbol",
                "symbol",
                "name",
                "isin",
                "exchange",
                "segment",
                "series",
                "instrument_type",
                "option_type",
                "lot_size",
                "tick_size",
                "freeze_qty",
                "upper_band",
                "lower_band",
                "expiry_date",
                "strike_price",
                "updated_at",
            ]

            # Intersect valid_cols with existing df columns
            final_cols = [c for c in valid_cols if c in df.columns]
            df = df[final_cols]

            df = df.replace({float("nan"): None})
            data_to_insert = df.to_dict(orient="records")
            logger.info(f"📊 Parsed {len(data_to_insert)} records.")

        except Exception as e:
            logger.error(f"❌ Parsing Error: {e}", exc_info=True)
            return

        # 5. Bulk Insert
        if data_to_insert:
            async with AsyncSessionLocal() as session:
                try:
                    logger.info("💾 Writing to Database...")
                    await session.execute(text("TRUNCATE TABLE instrument_master CASCADE;"))

                    chunk_size = 5000
                    for i in range(0, len(data_to_insert), chunk_size):
                        chunk = data_to_insert[i : i + chunk_size]
                        await session.execute(insert(InstrumentMaster), chunk)

                    await session.commit()
                    logger.info(f"✅ Successfully inserted {len(data_to_insert)} symbols.")
                except Exception as e:
                    await session.rollback()
                    logger.error(f"❌ Database Error: {e}")
                    return

        # 6. Reload Cache
        await self._load_cache()


master_data = MasterDataManager()
