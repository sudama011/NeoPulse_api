import pandas as pd
import numpy as np

def calculate_rsi(candles: list, period=14):
    if len(candles) < period + 1:
        return 50.0
    
    closes = pd.Series([c['close'] for c in candles])
    delta = closes.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def calculate_vwap(candles: list):
    if not candles:
        return 0.0
    df = pd.DataFrame(candles)
    df['pv'] = df['close'] * df['volume']
    vwap = df['pv'].cumsum() / df['volume'].cumsum()
    return vwap.iloc[-1]

# --- 🆕 NEW: EMA Calculation ---
def calculate_ema(candles: list, period=200):
    """Calculates Exponential Moving Average."""
    if len(candles) < period:
        return 0.0
    
    closes = pd.Series([c['close'] for c in candles])
    ema = closes.ewm(span=period, adjust=False).mean()
    return ema.iloc[-1]