import logging
import math
from collections import deque
from typing import Dict, List, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger("Toolbox")

# =========================================
# 1. STREAMING INDICATORS (LIVE TRADING - O(1))
# =========================================


class StreamingEMA:
    def __init__(self, period: int):
        self.period = period
        self.k = 2 / (period + 1)
        self.value = None

    def update(self, price: float) -> float:
        if self.value is None:
            self.value = price
        else:
            self.value = (price * self.k) + (self.value * (1 - self.k))
        return self.value


class StreamingSMA:
    """Simple Moving Average (O(1) update using Ring Buffer)"""

    def __init__(self, period: int):
        self.period = period
        self.buffer = deque(maxlen=period)
        self.sum = 0.0

    def update(self, price: float) -> float:
        if math.isnan(price):
            return 0.0

        self.buffer.append(price)
        self.sum += price

        if len(self.buffer) > self.period:
            removed = self.buffer.popleft()
            self.sum -= removed

        # If buffer isn't full, return partial average or 0 based on preference
        if len(self.buffer) < self.period:
            return self.sum / len(self.buffer)

        return self.sum / self.period


class StreamingRSI:
    """Relative Strength Index (Wilder's Smoothing)"""

    def __init__(self, period: int = 14):
        self.period = period
        self.prev_price = None
        self.avg_gain = 0.0
        self.avg_loss = 0.0
        self.initialized = False
        self.warmup_count = 0

    def update(self, price: float) -> float:
        if self.prev_price is None:
            self.prev_price = price
            return 50.0  # Neutral on first tick

        change = price - self.prev_price
        self.prev_price = price

        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        if not self.initialized:
            # Simple Average for the first 'period'
            self.avg_gain += gain
            self.avg_loss += loss
            self.warmup_count += 1

            if self.warmup_count >= self.period:
                self.avg_gain /= self.period
                self.avg_loss /= self.period
                self.initialized = True
        else:
            # Wilder's Smoothing (alpha = 1/period)
            self.avg_gain = ((self.avg_gain * (self.period - 1)) + gain) / self.period
            self.avg_loss = ((self.avg_loss * (self.period - 1)) + loss) / self.period

        if self.avg_loss == 0:
            return 100.0 if self.avg_gain > 0 else 50.0

        rs = self.avg_gain / self.avg_loss
        return 100.0 - (100.0 / (1.0 + rs))


class StreamingMACD:
    """MACD (12, 26, 9) Composition"""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast_ema = StreamingEMA(fast)
        self.slow_ema = StreamingEMA(slow)
        self.signal_ema = StreamingEMA(signal)

    def update(self, price: float) -> Tuple[float, float, float]:
        """Returns (macd_line, signal_line, histogram)"""
        f = self.fast_ema.update(price)
        s = self.slow_ema.update(price)

        macd_line = f - s
        signal_line = self.signal_ema.update(macd_line)
        hist = macd_line - signal_line

        return macd_line, signal_line, hist


class StreamingATR:
    """Average True Range (RMA Smoothing)"""

    def __init__(self, period: int = 14):
        self.period = period
        self.prev_close = None
        self.atr = 0.0
        self.initialized = False
        self.warmup_count = 0

    def update(self, high: float, low: float, close: float) -> float:
        if self.prev_close is None:
            self.prev_close = close
            return high - low

        tr1 = high - low
        tr2 = abs(high - self.prev_close)
        tr3 = abs(low - self.prev_close)
        tr = max(tr1, tr2, tr3)

        self.prev_close = close

        if not self.initialized:
            self.atr += tr
            self.warmup_count += 1
            if self.warmup_count >= self.period:
                self.atr /= self.period
                self.initialized = True
        else:
            # RMA (Pine Script Standard for ATR)
            alpha = 1.0 / self.period
            self.atr = (self.atr * (1 - alpha)) + (tr * alpha)

        return self.atr


class StreamingSupertrend:
    """Stateful Supertrend"""

    def __init__(self, period: int = 10, factor: float = 3.0):
        self.atr_ind = StreamingATR(period)
        self.factor = factor
        self.period = period

        # State
        self.final_upper = 0.0
        self.final_lower = 0.0
        self.trend = 1  # 1 = Up, -1 = Down
        self.prev_close = 0.0
        self.initialized = False

    def update(self, high: float, low: float, close: float) -> Tuple[float, int]:
        atr = self.atr_ind.update(high, low, close)

        if not self.atr_ind.initialized:
            self.prev_close = close
            return 0.0, 1

        hl2 = (high + low) / 2
        basic_upper = hl2 + (self.factor * atr)
        basic_lower = hl2 - (self.factor * atr)

        # Initialize bands on first run
        if not self.initialized:
            self.final_upper = basic_upper
            self.final_lower = basic_lower
            self.initialized = True
            self.prev_close = close
            return basic_lower, 1

        # Update Upper Band
        if basic_upper < self.final_upper or self.prev_close > self.final_upper:
            self.final_upper = basic_upper
        # Else keep previous (no change needed as self.final_upper persists)

        # Update Lower Band
        if basic_lower > self.final_lower or self.prev_close < self.final_lower:
            self.final_lower = basic_lower

        # Update Trend
        # Note: We use self.trend from PREVIOUS step to determine switch
        if self.trend == 1:
            if close < self.final_lower:
                self.trend = -1
        else:
            if close > self.final_upper:
                self.trend = 1

        self.prev_close = close

        val = self.final_lower if self.trend == 1 else self.final_upper
        return val, self.trend


# =========================================
# 2. STATIC TOOLBOX (BACKTESTING / UTILS)
# =========================================


class Toolbox:
    """
    Unified Technical Analysis Library (Pine Script Parity).

    Includes:
    1. Core Indicators (EMA, SMA, RSI, MACD, BB) - Accepting 'source' or 'candles'
    2. Complex Indicators (ATR, Supertrend, ADX, VWAP) - Requiring OHLCV 'candles'
    3. Price Patterns (Doji, Hammer, Engulfing)
    """

    # =========================================
    # 0. HELPER UTILITIES
    # =========================================

    @staticmethod
    def _extract(source: Union[List[float], List[Dict], pd.Series], key: str = "close") -> pd.Series:
        """
        Smart Helper: Auto-detects if input is a List of Candles or List of Prices.
        Allows passing 'candles' to simple indicators like EMA.
        """
        if isinstance(source, pd.Series):
            return source

        if not source:
            return pd.Series(dtype=float)

        # If user passed a list of Candle Dicts (Legacy Support)
        if isinstance(source, list) and len(source) > 0 and isinstance(source[0], dict):
            return pd.Series([c.get(key, 0.0) for c in source])

        # If user passed a list of Floats (Fastest)
        return pd.Series(source)

    # =========================================
    # 1. CORE INDICATORS (Vectorized)
    # =========================================

    @staticmethod
    def ema(source: Union[List[float], List[Dict]], length: int = 50) -> float:
        """Exponential Moving Average. Matches Pine: ta.ema(source, length)"""
        s = Toolbox._extract(source)
        if len(s) < length:
            return 0.0
        return float(s.ewm(span=length, adjust=False).mean().iloc[-1])

    @staticmethod
    def sma(source: Union[List[float], List[Dict]], length: int = 20) -> float:
        """Simple Moving Average. Matches Pine: ta.sma(source, length)"""
        s = Toolbox._extract(source)
        if len(s) < length:
            return 0.0
        return float(s.rolling(length).mean().iloc[-1])

    @staticmethod
    def rsi(source: Union[List[float], List[Dict]], length: int = 14) -> float:
        """Relative Strength Index. Matches Pine: ta.rsi(source, length)"""
        s = Toolbox._extract(source)
        if len(s) < length + 1:
            return 50.0

        delta = s.diff()
        # Pine Script uses RMA (Wilder's Smoothing) for RSI
        gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / length, adjust=False).mean()
        loss = -delta.where(delta < 0, 0.0).ewm(alpha=1 / length, adjust=False).mean()

        if float(loss.iloc[-1]) == 0:
            return 100.0

        rs = gain / loss
        return float(100 - (100 / (1 + rs)).iloc[-1])

    @staticmethod
    def macd(source: Union[List[float], List[Dict]], fast: int = 12, slow: int = 26, signal: int = 9):
        """Returns: (macd_line, signal_line, hist). Matches Pine: ta.macd(source...)"""
        s = Toolbox._extract(source)
        if len(s) < slow + signal:
            return 0.0, 0.0, 0.0

        ema_fast = s.ewm(span=fast, adjust=False).mean()
        ema_slow = s.ewm(span=slow, adjust=False).mean()

        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        hist = macd_line - signal_line

        return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(hist.iloc[-1])

    @staticmethod
    def bollinger_bands(source: Union[List[float], List[Dict]], length: int = 20, mult: float = 2.0):
        """Returns: (middle, upper, lower). Matches Pine: ta.bb(source...)"""
        s = Toolbox._extract(source)
        if len(s) < length:
            return 0.0, 0.0, 0.0

        basis = s.rolling(length).mean()
        dev = s.rolling(length).std()

        upper = basis + (mult * dev)
        lower = basis - (mult * dev)
        return float(basis.iloc[-1]), float(upper.iloc[-1]), float(lower.iloc[-1])

    # =========================================
    # 2. COMPLEX INDICATORS (Need OHLC)
    # =========================================

    @staticmethod
    def atr(candles: List[Dict], length: int = 14) -> float:
        """Average True Range. Needs full Candles."""
        if len(candles) < length + 1:
            return 0.0

        h = pd.Series([c["high"] for c in candles])
        l = pd.Series([c["low"] for c in candles])
        c = pd.Series([c["close"] for c in candles])

        tr1 = h - l
        tr2 = (h - c.shift(1)).abs()
        tr3 = (l - c.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # RMA smoothing (Pine Script standard for ATR)
        return float(tr.ewm(alpha=1 / length, adjust=False).mean().iloc[-1])

    @staticmethod
    def supertrend(candles: List[Dict], length: int = 10, factor: float = 3.0):
        """
        Stateful Supertrend. Returns: (Value, Trend [1=Up, -1=Down])
        matches ta.supertrend
        """
        if len(candles) < length:
            return 0.0, 1

        h = pd.Series([c["high"] for c in candles])
        l = pd.Series([c["low"] for c in candles])
        c = pd.Series([c["close"] for c in candles])

        # ATR Calc
        tr1 = h - l
        tr2 = (h - c.shift(1)).abs()
        tr3 = (l - c.shift(1)).abs()
        atr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).ewm(alpha=1 / length, adjust=False).mean()

        hl2 = (h + l) / 2
        basic_upper = hl2 + (factor * atr)
        basic_lower = hl2 - (factor * atr)

        final_upper = [0.0] * len(c)
        final_lower = [0.0] * len(c)
        trend = [1] * len(c)

        # Iterative Logic (Required for Supertrend state persistence)
        for i in range(1, len(c)):
            # Upper Band
            if basic_upper[i] < final_upper[i - 1] or c[i - 1] > final_upper[i - 1]:
                final_upper[i] = basic_upper[i]
            else:
                final_upper[i] = final_upper[i - 1]

            # Lower Band
            if basic_lower[i] > final_lower[i - 1] or c[i - 1] < final_lower[i - 1]:
                final_lower[i] = basic_lower[i]
            else:
                final_lower[i] = final_lower[i - 1]

            # Trend Switch
            prev_trend = trend[i - 1]
            if prev_trend == 1:
                trend[i] = -1 if c[i] < final_lower[i] else 1
            else:
                trend[i] = 1 if c[i] > final_upper[i] else -1

        val = final_lower[-1] if trend[-1] == 1 else final_upper[-1]
        return float(val), trend[-1]

    @staticmethod
    def adx(candles: List[Dict], length: int = 14) -> float:
        """Average Directional Index (Trend Strength)"""
        if len(candles) < length * 2:
            return 0.0

        df = pd.DataFrame(candles)
        # Ensure keys exist
        if "high" not in df or "low" not in df:
            return 0.0

        df["up"] = df["high"].diff()
        df["down"] = -df["low"].diff()

        df["tr"] = np.maximum(
            df["high"] - df["low"],
            np.maximum(abs(df["high"] - df["close"].shift(1)), abs(df["low"] - df["close"].shift(1))),
        )

        df["pdm"] = np.where((df["up"] > df["down"]) & (df["up"] > 0), df["up"], 0.0)
        df["ndm"] = np.where((df["down"] > df["up"]) & (df["down"] > 0), df["down"], 0.0)

        # Smooth (Wilder's)
        alpha = 1 / length
        tr_s = df["tr"].ewm(alpha=alpha, adjust=False).mean()
        pdm_s = df["pdm"].ewm(alpha=alpha, adjust=False).mean()
        ndm_s = df["ndm"].ewm(alpha=alpha, adjust=False).mean()

        pdi = 100 * (pdm_s / tr_s)
        ndi = 100 * (ndm_s / tr_s)
        dx = 100 * abs(pdi - ndi) / (pdi + ndi)

        return float(dx.ewm(alpha=alpha, adjust=False).mean().iloc[-1])

    @staticmethod
    def vwap(candles: List[Dict]) -> float:
        """Volume Weighted Average Price"""
        if not candles:
            return 0.0
        df = pd.DataFrame(candles)
        # Vwap = Sum(Price * Vol) / Sum(Vol)
        return float(((df["close"] * df["volume"]).cumsum() / df["volume"].cumsum()).iloc[-1])

    # =========================================
    # 3. PATTERNS (Legacy Support)
    # =========================================

    @staticmethod
    def is_doji(candle: Dict, threshold=0.05) -> bool:
        """Body < 5% of Range"""
        rng = candle["high"] - candle["low"]
        if rng == 0:
            return True
        body = abs(candle["close"] - candle["open"])
        return (body / rng) <= threshold

    @staticmethod
    def is_hammer(candle: Dict) -> bool:
        """Lower wick > 2x Body, small upper wick"""
        body = abs(candle["close"] - candle["open"])
        lower_wick = min(candle["close"], candle["open"]) - candle["low"]
        upper_wick = candle["high"] - max(candle["close"], candle["open"])
        return lower_wick >= (2 * body) and upper_wick < body

    @staticmethod
    def is_engulfing(curr: Dict, prev: Dict) -> int:
        """1=Bullish, -1=Bearish, 0=None"""
        # Bullish: Prev Red, Curr Green, Curr Open < Prev Close, Curr Close > Prev Open
        if (prev["close"] < prev["open"]) and (curr["close"] > curr["open"]):
            if curr["close"] > prev["open"] and curr["open"] < prev["close"]:
                return 1
        # Bearish: Prev Green, Curr Red
        if (prev["close"] > prev["open"]) and (curr["close"] < curr["open"]):
            if curr["close"] < prev["open"] and curr["open"] > prev["close"]:
                return -1
        return 0


# Global Instance
tb = Toolbox()
