import pandas as pd
import numpy as np

def calculate_rsi(candles: list, period=14):
    """Calculates RSI on the 'close' price of candles list."""
    if len(candles) < period + 1:
        return 50.0 # Neutral if not enough data
    
    closes = pd.Series([c['close'] for c in candles])
    delta = closes.diff()
    
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def calculate_vwap(candles: list):
    """Calculates Intraday VWAP."""
    if not candles:
        return 0.0
    
    df = pd.DataFrame(candles)
    # VWAP = Cumulative(Price * Vol) / Cumulative(Vol)
    df['pv'] = df['close'] * df['volume']
    vwap = df['pv'].cumsum() / df['volume'].cumsum()
    return vwap.iloc[-1]