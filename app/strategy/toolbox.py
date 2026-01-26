import pandas as pd


class Toolbox:
    """
    Unified Technical Analysis Library.
    Includes: Indicators & Pattern Recognition.
    """

    # --- INDICATORS ---
    @staticmethod
    def rsi(candles: list, period=14) -> float:
        if len(candles) < period + 1:
            return 50.0
        closes = pd.Series([c["close"] for c in candles])
        delta = closes.diff()
        gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / period, adjust=False).mean()
        loss = -delta.where(delta < 0, 0.0).ewm(alpha=1 / period, adjust=False).mean()
        return float(100 - (100 / (1 + (gain / loss))).iloc[-1])

    @staticmethod
    def ema(candles: list, period=50) -> float:
        if len(candles) < period:
            return 0.0
        return float(pd.Series([c["close"] for c in candles]).ewm(span=period, adjust=False).mean().iloc[-1])

    @staticmethod
    def sma(candles: list, period=20) -> float:
        if len(candles) < period:
            return 0.0
        return float(pd.Series([c["close"] for c in candles]).rolling(period).mean().iloc[-1])

    @staticmethod
    def vwap(candles: list) -> float:
        if not candles:
            return 0.0
        df = pd.DataFrame(candles)
        return float(((df["close"] * df["volume"]).cumsum() / df["volume"].cumsum()).iloc[-1])

    @staticmethod
    def bollinger_bands(candles: list, period=20, std=2.0):
        if len(candles) < period:
            return 0.0, 0.0
        closes = pd.Series([c["close"] for c in candles])
        sma = closes.rolling(period).mean()
        dev = closes.rolling(period).std()
        return float((sma + std * dev).iloc[-1]), float((sma - std * dev).iloc[-1])

    @staticmethod
    def supertrend(candles: list, period=7, multiplier=3.0):
        """Returns: (Value, Trend [1=Up, -1=Down])"""
        if len(candles) < period:
            return 0.0, 1
        df = pd.DataFrame(candles)
        hl2 = (df["high"] + df["low"]) / 2
        atr = (df["high"] - df["low"]).rolling(period).mean()

        # Simplified calculation for performance
        upper = hl2 + (multiplier * atr)
        lower = hl2 - (multiplier * atr)

        # Determine trend based on latest close
        close = df["close"].iloc[-1]
        trend = 1 if close > lower.iloc[-1] else -1
        level = lower.iloc[-1] if trend == 1 else upper.iloc[-1]
        return float(level), trend

    # --- PATTERNS ---
    @staticmethod
    def is_doji(candle: dict, threshold=0.05) -> bool:
        """Body is < 5% of total range"""
        rng = candle["high"] - candle["low"]
        body = abs(candle["close"] - candle["open"])
        return rng > 0 and (body / rng) <= threshold

    @staticmethod
    def is_hammer(candle: dict) -> bool:
        """Lower wick > 2x Body, Small Upper Wick"""
        body = abs(candle["close"] - candle["open"])
        lower_wick = min(candle["close"], candle["open"]) - candle["low"]
        return lower_wick >= (2 * body)

    @staticmethod
    def is_engulfing(curr: dict, prev: dict) -> int:
        """Returns: 1 (Bullish), -1 (Bearish), 0 (None)"""
        if (prev["close"] < prev["open"]) and (curr["close"] > curr["open"]):  # Bullish
            if curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
                return 1
        if (prev["close"] > prev["open"]) and (curr["close"] < curr["open"]):  # Bearish
            if curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
                return -1
        return 0
