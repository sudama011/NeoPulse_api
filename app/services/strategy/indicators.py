import numpy as np
import pandas as pd


def calculate_rsi(candles: list, period=14):
    """Calculates RSI using Wilder's Smoothing."""
    if len(candles) < period + 1:
        return 50.0

    # Create Series
    closes = pd.Series([c["close"] for c in candles])
    delta = closes.diff()

    # Separate gains/losses
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    # Wilder's Smoothing (alpha = 1/n)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    # Handle NaN/Inf
    rsi = rsi.fillna(50.0)
    return float(rsi.iloc[-1])


def calculate_vwap(candles: list):
    """Calculates Volume Weighted Average Price."""
    if not candles:
        return 0.0
    df = pd.DataFrame(candles)
    df["pv"] = df["close"] * df["volume"]
    vwap = df["pv"].cumsum() / df["volume"].cumsum()
    return float(vwap.iloc[-1])


def calculate_ema(candles: list, period=50):
    """Calculates Exponential Moving Average."""
    if len(candles) < period:
        return 0.0
    closes = pd.Series([c["close"] for c in candles])
    ema = closes.ewm(span=period, adjust=False).mean()
    return float(ema.iloc[-1])


def calculate_sma(candles: list, period=20):
    """Calculates Simple Moving Average."""
    if len(candles) < period:
        return 0.0
    closes = [c["close"] for c in candles[-period:]]
    return float(sum(closes) / len(closes))


def calculate_bollinger_bands(candles: list, period=20, std_dev=2.0):
    """Calculates Bollinger Bands (Upper, Lower)."""
    if len(candles) < period:
        return 0.0, 0.0

    closes = pd.Series([c["close"] for c in candles])
    sma = closes.rolling(window=period).mean()
    std = closes.rolling(window=period).std()

    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)

    return float(upper.iloc[-1]), float(lower.iloc[-1])
