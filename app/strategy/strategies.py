from app.strategy.base import BaseStrategy, RiskManager
from app.strategy.toolbox import Toolbox as T
from app.core.executors import run_blocking


# --- 1. GENERIC RULE ENGINE (Configuration Based) ---
class RuleBasedStrategy(BaseStrategy):
    """
    Executes trades based on a JSON configuration.
    Example Config:
    {
        "buy_conditions": [{"ind": "rsi", "op": "<", "val": 30}],
        "sell_conditions": [{"ind": "rsi", "op": ">", "val": 70}]
    }
    """

    def __init__(self, symbol, token, risk_manager: RiskManager, config: dict):
        super().__init__("RULE_ENGINE", symbol, token, risk_manager)
        self.config = config

    async def logic(self, candle: dict):
        if len(self.candles) < 50:
            return

        # Calculate Indicators needed
        rsi = T.rsi(self.candles)
        ema = T.ema(self.candles)

        close = candle["close"]

        # Evaluate BUY
        if self.position == 0:
            buy_signal = True
            for rule in self.config.get("buy_conditions", []):
                val = rsi if rule["ind"] == "rsi" else ema
                if rule["op"] == "<" and not (val < rule["val"]):
                    buy_signal = False
                if rule["op"] == ">" and not (val > rule["val"]):
                    buy_signal = False

            if buy_signal:
                await self.execute_order("BUY", close, "RULE_BUY")

        # Evaluate SELL (Exit)
        elif self.position > 0:
            sell_signal = False
            for rule in self.config.get("sell_conditions", []):
                val = rsi if rule["ind"] == "rsi" else ema
                if rule["op"] == ">" and (val > rule["val"]):
                    sell_signal = True

            if sell_signal:
                await self.execute_order("SELL", close, "RULE_SELL")


# --- 2. MOMENTUM STRATEGY ---
class MomentumStrategy(BaseStrategy):
    def __init__(self, symbol, token, risk_manager: RiskManager):
        super().__init__("MOMENTUM", symbol, token, risk_manager)

    async def logic(self, candle: dict):
        if len(self.candles) < 50:
            return

        # Thread-safe indicator calculation
        rsi = await run_blocking(T.rsi, self.candles, 14)
        ema = await run_blocking(T.ema, self.candles, 50)
        vwap = await run_blocking(T.vwap, self.candles)
        close = candle["close"]

        # Entry
        if self.position == 0:
            if close > ema and rsi > 60 and close > vwap:
                await self.execute_order("BUY", close, "MOMENTUM_LONG")
            elif close < ema and rsi < 40 and close < vwap:
                await self.execute_order("SELL", close, "MOMENTUM_SHORT")

        # Exit (Fixed Targets + Trailing handled in Base)
        elif self.position != 0:
            pnl_pct = (close - self.entry_price) / self.entry_price
            if self.position < 0:
                pnl_pct *= -1

            if pnl_pct > 0.01:
                await self.execute_order(
                    "SELL" if self.position > 0 else "BUY", close, "TP"
                )
            elif pnl_pct < -0.005:
                await self.execute_order(
                    "SELL" if self.position > 0 else "BUY", close, "SL"
                )


# --- 3. OPENING RANGE BREAKOUT (ORB) ---
class ORBStrategy(BaseStrategy):
    def __init__(self, symbol, token, risk_manager: RiskManager):
        super().__init__("ORB", symbol, token, risk_manager)
        self.range_high = 0.0
        self.range_low = 0.0
        self.range_set = False

    async def logic(self, candle: dict):
        t = candle["start_time"]

        # 9:15 - 9:30: Build Range
        if t.hour == 9 and t.minute < 30:
            self.range_high = max(self.range_high, candle["high"])
            self.range_low = (
                min(self.range_low, candle["low"])
                if self.range_low > 0
                else candle["low"]
            )
            return

        if not self.range_set:
            self.range_set = True  # Range locked at 9:30

        # Breakout Logic
        if self.position == 0:
            if candle["close"] > self.range_high:
                await self.execute_order("BUY", candle["close"], "ORB_BREAKOUT")
            elif candle["close"] < self.range_low:
                await self.execute_order("SELL", candle["close"], "ORB_BREAKDOWN")


# --- 4. MEAN REVERSION ---
class MeanReversionStrategy(BaseStrategy):
    def __init__(self, symbol, token, risk_manager: RiskManager):
        super().__init__("MEAN_REVERSION", symbol, token, risk_manager)

    async def logic(self, candle: dict):
        if len(self.candles) < 20:
            return
        upper, lower = await run_blocking(T.bollinger_bands, self.candles)
        rsi = await run_blocking(T.rsi, self.candles)
        close = candle["close"]

        if self.position == 0:
            if close < lower and rsi < 30:
                await self.execute_order("BUY", close, "OVERSOLD")
            elif close > upper and rsi > 70:
                await self.execute_order("SELL", close, "OVERBOUGHT")
