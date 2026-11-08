"""
Intraday history from Kotak Neo for longer validation than Yahoo allows (Yahoo: ~60 days of
5m/15m). Uses your .env credentials and the instrument master in your database
(run `make sync` first). Results are cached in backtest/.cache.
"""

import logging
import os
import pickle
from datetime import timedelta

from app.broker.kotak_session import KotakSession
from app.core.clock import today_ist
from app.core.executors import global_executor
from app.core.settings import settings
from app.db.session import create_engine, create_session_factory
from app.domain.models import Candle
from app.market.history import HistoryService
from app.market.instruments import InstrumentStore

logger = logging.getLogger("KotakFeed")
_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")


async def load_kotak_candles(symbols: list[str], days: int, interval_min: int) -> dict[str, list[Candle]]:
    global_executor.start()
    engine = create_engine(settings)
    try:
        store = InstrumentStore(create_session_factory(engine))
        await store.load()
        history = HistoryService(KotakSession(settings))
        end = today_ist()
        start = end - timedelta(days=days)
        out: dict[str, list[Candle]] = {}
        for sym in symbols:
            info = store.get(sym)
            if info is None:
                logger.warning("skip %s: not in instrument master", sym)
                continue
            cache = os.path.join(_CACHE_DIR, f"kotak_{info.token}_{interval_min}_{start}_{end}.pkl")
            if os.path.exists(cache):
                with open(cache, "rb") as fh:
                    out[sym] = pickle.load(fh)
                continue
            try:
                candles = await history.range(info.token, interval_min, start, end, info.segment)
            except Exception as e:
                logger.warning("skip %s: %s", sym, e)
                continue
            os.makedirs(_CACHE_DIR, exist_ok=True)
            with open(cache, "wb") as fh:
                pickle.dump(candles, fh)
            out[sym] = candles
            logger.info("%s: %d candles from Kotak", sym, len(candles))
        return out
    finally:
        await engine.dispose()
        global_executor.stop()
