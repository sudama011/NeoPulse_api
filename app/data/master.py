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
    Stores full instrument metadata in memory for high-speed access.
    """

    def __init__(self):
        # Key: Trading Symbol (e.g. 'NIFTY25JANFUT')
        # Value: Dict {token, lot_size, freeze_qty, tick_size, ...}
        self._cache = {}
        self._token_map = {}  # Reverse lookup (Token -> Symbol)
        self.is_loaded = False

    async def initialize(self):
        """Loads data from DB into Memory on startup."""
        if not self.is_loaded:
            await self._load_cache()
            logger.info(f"📚 Master Data Loaded: {len(self._cache)} instruments in memory.")

    def get_token(self, symbol: str) -> str:
        """Returns Instrument Token (string)"""
        data = self._cache.get(symbol)
        return str(data["token"]) if data else None

    def get_symbol(self, token: str) -> str:
        """Returns Trading Symbol"""
        return self._token_map.get(str(token))

    def get_data(self, symbol: str) -> dict:
        """Returns full instrument metadata (Lot size, Freeze Qty, etc.)"""
        return self._cache.get(symbol)

    async def _load_cache(self):
        async with AsyncSessionLocal() as session:
            # Select all columns we need
            result = await session.execute(select(InstrumentMaster))
            instruments = result.scalars().all()

            self._cache.clear()
            self._token_map.clear()

            for i in instruments:
                token_str = str(i.token)

                # Store critical execution data
                self._cache[i.symbol] = {
                    "token": token_str,
                    "trading_symbol": i.trading_symbol,
                    "lot_size": i.lot_size or 1,
                    "tick_size": i.tick_size or 0.05,
                    "freeze_qty": i.freeze_qty or 1800,  # Default high if missing
                    "upper_band": i.upper_band,
                    "lower_band": i.lower_band,
                }

                self._token_map[token_str] = i.trading_symbol

            self.is_loaded = True

    async def sync_daily_script(self):
        """
        1. Gets URL from Kotak SDK.
        2. Downloads CSV via HTTPX.
        3. Parses & Inserts via Pandas with Precision Correction.
        """
        logger.info("🌍 Starting Scrip Master Sync...")
        try:
            await kotak_adapter.login()
            logger.info("📡 Fetching Master URL from Kotak...")

            async with kotak_limiter:
                csv_url = await asyncio.to_thread(kotak_adapter.client.scrip_master, exchange_segment=EXCHANGE_NSE)

            logger.info(f"⬇️ Downloading CSV from: {csv_url}")
            async with httpx.AsyncClient() as client:
                resp = await client.get(csv_url, timeout=30.0)
                resp.raise_for_status()
                csv_content = resp.content
        except Exception as e:
            logger.critical(f"❌ Download Failed: {e}")
            return
        try:
            logger.info("🔄 Parsing CSV Data...")
            df = pd.read_csv(io.BytesIO(csv_content))

            # --- PRECISION CORRECTION ---
            df["lPrecision"] = pd.to_numeric(df["lPrecision"], errors="coerce").fillna(2)
            df["divider"] = 10 ** df["lPrecision"]
            price_cols = ["dHighPriceRange", "dLowPriceRange", "dTickSize", "dStrikePrice"]
            for col in price_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
                    df[col] = df[col] / df["divider"]

            # --- RENAME & TRANSFORM ---
            df = df.rename(
                columns={
                    "pSymbol": "token",
                    "pTrdSymbol": "trading_symbol",
                    "pSymbolName": "symbol",
                    "pDesc": "name",
                    "pISIN": "isin",
                    "pExchSeg": "segment",
                    "pExchange": "exchange",
                    "pGroup": "series",
                    "pInstType": "instrument_type",
                    "pOptionType": "option_type",
                    "lLotSize": "lot_size",
                    "dTickSize": "tick_size",
                    "lFreezeQty": "freeze_qty",
                    "dHighPriceRange": "upper_band",
                    "dLowPriceRange": "lower_band",
                    "lExpiryDate": "expiry_date",
                    "dStrikePrice": "strike_price",
                }
            )

            # Defaults & Timezone
            df["updated_at"] = pd.Timestamp.now(tz="UTC")
            df["exchange"] = df.get("exchange", "NSE").fillna("NSE")
            df["token"] = pd.to_numeric(df["token"], errors="coerce")
            df = df.dropna(subset=["token", "trading_symbol"])

            # --- DATE PARSING ---
            if "expiry_date" in df.columns:
                df["expiry_date"] = pd.to_numeric(df["expiry_date"], errors="coerce")
                df["expiry_date"] = df["expiry_date"].apply(
                    lambda x: datetime.fromtimestamp(x).date() if x > 0 else None
                )

            # Filter & Replace NaN
            valid_cols = [
                "token",
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

            df = df[[c for c in valid_cols if c in df.columns]].replace({float("nan"): None})
            data_to_insert = df.to_dict(orient="records")
        except Exception as e:
            logger.error(f"❌ Parsing Error: {e}", exc_info=True)
            return
        # Bulk Insert
        if data_to_insert:
            try:
                async with AsyncSessionLocal() as session:

                    await session.execute(text("TRUNCATE TABLE instrument_master CASCADE;"))

                    chunk_size = 5000
                    for i in range(0, len(data_to_insert), chunk_size):
                        await session.execute(insert(InstrumentMaster), data_to_insert[i: i + chunk_size])

                    await session.commit()
                    logger.info(f"✅ Synced {len(data_to_insert)} records.")

            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Database Error: {e}")
                return

        await self._load_cache()


master_data = MasterDataManager()
