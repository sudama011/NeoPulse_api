"""Instrument master: symbol <-> token lookup plus tick/lot metadata, cached in memory."""

import io
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx
import pandas as pd
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.broker.kotak_session import KotakSession, response_error
from app.models import Instrument

logger = logging.getLogger("Instruments")


@dataclass(frozen=True, slots=True)
class InstrumentInfo:
    token: str
    trading_symbol: str
    segment: str = "nse_cm"
    lot_size: int = 1
    tick_size: float = 0.05

    def round_price(self, price: float) -> float:
        """Rounds to the instrument's tick size (exchanges reject off-tick limit prices)."""
        tick = self.tick_size or 0.05
        return round(round(price / tick) * tick, 2)

    def round_qty(self, qty: float) -> int:
        lot = max(1, self.lot_size)
        return int(math.floor(qty / lot) * lot)


class InstrumentStore:
    def __init__(self, session_factory: async_sessionmaker):
        self._sf = session_factory
        self._by_symbol: dict[str, InstrumentInfo] = {}
        self._by_token: dict[str, InstrumentInfo] = {}

    @property
    def count(self) -> int:
        return len(self._by_symbol)

    async def load(self) -> None:
        async with self._sf() as db:
            rows = (await db.execute(select(Instrument))).scalars().all()
        self._by_symbol.clear()
        self._by_token.clear()
        for r in rows:
            info = InstrumentInfo(
                token=str(r.token),
                trading_symbol=r.trading_symbol,
                segment=r.segment or "nse_cm",
                lot_size=r.lot_size or 1,
                tick_size=float(r.tick_size or 0.05),
            )
            self._by_symbol[info.trading_symbol.upper()] = info
            self._by_token[info.token] = info
        logger.info("📚 %d instruments loaded", len(self._by_symbol))

    def get(self, symbol: str) -> Optional[InstrumentInfo]:
        """Accepts 'RELIANCE-EQ' or plain 'RELIANCE' (resolved to the EQ series)."""
        key = symbol.strip().upper()
        return self._by_symbol.get(key) or self._by_symbol.get(f"{key}-EQ")

    def by_token(self, token: str) -> Optional[InstrumentInfo]:
        return self._by_token.get(str(token))

    async def last_synced(self) -> Optional[datetime]:
        async with self._sf() as db:
            return (await db.execute(select(func.max(Instrument.updated_at)))).scalar()

    # ------------------------------------------------------------------ daily sync
    async def sync_from_broker(self, kotak: KotakSession, segment: str = "nse_cm") -> int:
        """Downloads today's scrip master and atomically replaces the table. Returns row count."""
        url = await kotak.call(kotak.client.scrip_master, exchange_segment=segment)
        if not isinstance(url, str):
            raise RuntimeError(f"scrip_master failed: {response_error(url) or url}")

        logger.info("⬇️ Downloading scrip master %s", url)
        async with httpx.AsyncClient(timeout=60.0) as http:
            resp = await http.get(url)
            resp.raise_for_status()

        records = self._parse_csv(resp.content)
        if not records:
            raise RuntimeError("scrip master is empty")

        async with self._sf() as db, db.begin():
            await db.execute(delete(Instrument))
            for i in range(0, len(records), 5000):
                await db.execute(insert(Instrument), records[i : i + 5000])

        await self.load()
        logger.info("✅ Synced %d instruments", len(records))
        return len(records)

    @staticmethod
    def _parse_csv(content: bytes) -> list[dict]:
        df = pd.read_csv(io.BytesIO(content), low_memory=False)
        df.columns = [c.strip().rstrip(";") for c in df.columns]

        precision = pd.to_numeric(df.get("lPrecision", 2), errors="coerce").fillna(2)
        divider = 10**precision
        tick = pd.to_numeric(df.get("dTickSize", 5), errors="coerce").fillna(5) / divider

        out = pd.DataFrame(
            {
                "token": pd.to_numeric(df["pSymbol"], errors="coerce"),
                "trading_symbol": df["pTrdSymbol"].astype(str).str.strip(),
                "symbol": df.get("pSymbolName", df["pTrdSymbol"]).astype(str).str.strip(),
                "name": df.get("pDesc"),
                "segment": df.get("pExchSeg", "nse_cm"),
                "series": df.get("pGroup"),
                "lot_size": pd.to_numeric(df.get("lLotSize", 1), errors="coerce").fillna(1).astype(int),
                "tick_size": tick.round(4),
            }
        )
        out = out.dropna(subset=["token"]).drop_duplicates(subset=["token"])
        out["token"] = out["token"].astype("int64")
        out = out.astype(object).where(pd.notna(out), None)
        synced_at = datetime.now(timezone.utc)
        records = out.to_dict(orient="records")
        for r in records:
            r["updated_at"] = synced_at
        return records
