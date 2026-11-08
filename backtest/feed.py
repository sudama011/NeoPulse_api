"""Historical candles for backtests (Yahoo Finance; no broker login required)."""

import logging
import os
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from app.core.clock import IST
from app.domain.models import Candle

logger = logging.getLogger("BacktestFeed")

DAILY = 1440
_YF_INTERVALS = {1: "1m", 2: "2m", 5: "5m", 15: "15m", 30: "30m", 60: "60m", DAILY: "1d"}
_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")


def load_candles(symbol: str, days: int, interval_min: int) -> tuple[list[Candle], pd.DataFrame]:
    """
    Returns (candles, dataframe). Yahoo limits intraday history: 1m ~7 days, 5m/15m/30m ~60 days,
    60m ~730 days; daily (interval 1440) goes back decades. Downloads are cached per day.
    """
    if interval_min not in _YF_INTERVALS:
        raise ValueError(f"interval must be one of {sorted(_YF_INTERVALS)}")
    base = symbol.upper().removesuffix("-EQ")
    ticker = base if base.endswith(".NS") else f"{base}.NS"
    end = datetime.now()
    cache = os.path.join(_CACHE_DIR, f"{ticker}_{interval_min}_{days}_{end:%Y%m%d}.pkl")
    if os.path.exists(cache):
        df = repair_splits(pd.read_pickle(cache), ticker)
        return _to_candles(df), df
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

    df = repair_splits(df, ticker)
    os.makedirs(_CACHE_DIR, exist_ok=True)
    df.to_pickle(cache)
    logger.info("Loaded %d candles for %s", len(df), ticker)
    return _to_candles(df), df


def _to_candles(df: pd.DataFrame) -> list[Candle]:
    return [
        Candle(ts.to_pydatetime(), float(r.open), float(r.high), float(r.low), float(r.close), int(r.volume))
        for ts, r in df.iterrows()
        if r.close > 0
    ]


_SPLIT_RATIOS = (2, 3, 4, 5, 10, 20, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 1 / 20)


def repair_splits(df: pd.DataFrame, ticker: str = "") -> pd.DataFrame:
    """
    Yahoo sometimes leaves corporate-action splits unadjusted (e.g. NIFTYBEES/BANKBEES 2019).
    A one-bar move that matches a clean split ratio within 3% is treated as a split and all
    earlier OHLC (and volume) are rescaled. Real one-day moves of 50%+ in large caps are rare
    enough that this heuristic is safe for backtests; each repair is logged.
    """
    df = df.copy()
    ratio = (df["close"] / df["close"].shift(1)).fillna(1.0)
    for ts, r in ratio[(ratio > 1.8) | (ratio < 0.55)].items():
        match = next((k for k in _SPLIT_RATIOS if abs(r / k - 1) < 0.03), None)
        if match is None:
            continue
        earlier = df.index < ts
        df.loc[earlier, ["open", "high", "low", "close"]] *= match
        df.loc[earlier, "volume"] = (df.loc[earlier, "volume"] / match).round()
        logger.warning("Repaired unadjusted split in %s at %s (ratio %.3g)", ticker, ts.date(), match)
    return df
