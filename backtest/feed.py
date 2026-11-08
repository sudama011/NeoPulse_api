"""Historical candles for backtests (Yahoo Finance; no broker login required)."""

import logging
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from app.core.clock import IST
from app.domain.models import Candle

logger = logging.getLogger("BacktestFeed")

_YF_INTERVALS = {1: "1m", 2: "2m", 5: "5m", 15: "15m", 30: "30m", 60: "60m"}


def load_candles(symbol: str, days: int, interval_min: int) -> tuple[list[Candle], pd.DataFrame]:
    """Returns (candles, dataframe). Yahoo limits intraday history (~60 days, 7 for 1m)."""
    if interval_min not in _YF_INTERVALS:
        raise ValueError(f"interval must be one of {sorted(_YF_INTERVALS)}")
    base = symbol.upper().removesuffix("-EQ")
    ticker = base if base.endswith(".NS") else f"{base}.NS"
    end = datetime.now()
    df = yf.download(
        ticker,
        start=end - timedelta(days=days),
        end=end,
        interval=_YF_INTERVALS[interval_min],
        auto_adjust=False,
        progress=False,
    )
    if df.empty:
        raise RuntimeError(f"no data from Yahoo for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
    idx = pd.to_datetime(df.index)
    df.index = idx.tz_localize("UTC").tz_convert(IST) if idx.tz is None else idx.tz_convert(IST)

    candles = [
        Candle(ts.to_pydatetime(), float(r.open), float(r.high), float(r.low), float(r.close), int(r.volume))
        for ts, r in df.iterrows()
    ]
    logger.info("Loaded %d candles for %s", len(candles), ticker)
    return candles, df
