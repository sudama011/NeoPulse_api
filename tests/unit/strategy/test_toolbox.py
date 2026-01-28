import pytest
import pandas as pd
import numpy as np
from app.strategy.toolbox import tb

class TestToolbox:
    def test_extract_helper(self):
        # Case 1: List of Floats
        data_floats = [10.0, 11.0, 12.0]
        res1 = tb._extract(data_floats)
        assert isinstance(res1, pd.Series)
        assert res1.iloc[-1] == 12.0

        # Case 2: List of Candles
        data_candles = [{"close": 10.0}, {"close": 11.0}, {"close": 12.0}]
        res2 = tb._extract(data_candles)
        assert res2.iloc[-1] == 12.0

    def test_ema_calculation(self):
        prices = [10, 11, 12, 13, 14]
        # EMA(2) should weight recent prices heavily
        ema_val = tb.ema(prices, length=2)
        assert ema_val > 13.0 and ema_val < 14.0

    def test_rsi_calculation(self):
        # Create a sequence of gains
        prices = [100.0 + i for i in range(20)] 
        rsi = tb.rsi(prices, length=14)
        # Constant gain -> RSI should be near 100
        assert rsi > 70.0

    def test_supertrend_logic(self):
        # Mock uptrend candles
        candles = []
        for i in range(20):
            candles.append({
                "high": 100 + i + 2,
                "low": 100 + i - 2,
                "close": 100 + i
            })
        
        val, trend = tb.supertrend(candles, length=10, factor=3.0)
        assert trend == 1  # Should be Uptrend
        assert val < candles[-1]["close"] # Support line below price

    def test_patterns(self):
        # Doji
        doji = {"open": 100, "close": 100.1, "high": 105, "low": 95}
        assert tb.is_doji(doji) == True
        
        # Hammer
        hammer = {"open": 100, "close": 102, "high": 102.5, "low": 90}
        assert tb.is_hammer(hammer) == True