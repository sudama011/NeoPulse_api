import logging
import operator
from typing import Any, Dict

from app.strategy import register_strategy
from app.strategy.base import BaseStrategy
from app.strategy.toolbox import Toolbox as T

logger = logging.getLogger("GenericStrategy")


@register_strategy("GENERIC")
@register_strategy("RULE_ENGINE")
class GenericStrategy(BaseStrategy):
    """
    A 'Zero-Code' Strategy Engine.
    Executes trades based on a JSON configuration dictionary.

    Capabilities:
    - Compare Indicator vs Value (RSI < 30)
    - Compare Indicator vs Indicator (EMA_20 > EMA_50)
    - Compare Price vs Indicator (Close > Supertrend)
    """

    # Map string symbols to python operators
    OPS = {">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le, "==": operator.eq}

    def __init__(self, name: str, symbol: str, token: str, params: Dict[str, Any] = None):
        """
        params["rules"] example:
        {
            "buy_rules": [
                {"ind1": "ema", "period1": 20, "op": ">", "ind2": "ema", "period2": 50},
                {"ind1": "rsi", "period1": 14, "op": ">", "val": 50}
            ],
            "sell_rules": [...]
        }
        """
        super().__init__(name, symbol, token, params)
        self.rules_config = params.get("rules", params) if params else {}

    async def on_candle(self, candle: Dict[str, Any]):
        """Override: candle-based logic for rule engine."""
        self.candles.append(candle)

        # Need enough data to calculate indicators
        if len(self.candles) < self.WARMUP_PERIOD:
            return

        close = candle["close"]

        # 1. Check BUY Conditions (All must be True - 'AND' logic)
        if self.position == 0:
            if self._evaluate_rules(self.rules_config.get("buy_rules", []), candle):
                await self.buy(price=close, tag="GENERIC_BUY")

        # 2. Check SELL/EXIT Conditions
        elif self.position > 0:
            if self._evaluate_rules(self.rules_config.get("sell_rules", []), candle):
                await self.sell(price=close, tag="GENERIC_EXIT")

    async def on_tick(self, tick: Dict[str, Any]):
        """Tick mode: convert to candle and use on_candle logic."""
        candle = tick.get("_candle")
        if candle:
            # Already have a candle from BaseStrategy.on_candle
            return
        # In live mode, we'd need a candle aggregator — for now, skip
        pass

    def _evaluate_rules(self, rules: list, candle: dict) -> bool:
        """Returns True if ALL rules pass."""
        if not rules:
            return False

        for rule in rules:
            if not self._check_rule(rule, candle):
                return False
        return True

    def _check_rule(self, rule: dict, candle: dict) -> bool:
        """Evaluates a single rule dictionary."""
        try:
            op_func = self.OPS.get(rule.get("op", ">"))

            # --- FETCH LEFT SIDE VALUE ---
            val1 = self._get_value(rule.get("lhs_type", "indicator"), rule, "1")

            # --- FETCH RIGHT SIDE VALUE ---
            # Can be a static value (30, 50, 0) or another indicator
            if "val" in rule:
                val2 = rule["val"]
            else:
                val2 = self._get_value(rule.get("rhs_type", "indicator"), rule, "2")

            return op_func(val1, val2)

        except Exception as e:
            logger.error(f"Rule Eval Error: {e} | Rule: {rule}")
            return False

    def _get_value(self, source_type: str, rule: dict, suffix: str = "") -> float:
        """Helper to fetch values dynamically from Toolbox or Candle."""
        candle_list = list(self.candles)

        # 1. Price Value (Close, Open, High, Low)
        if source_type == "price":
            key = rule.get(f"price_key{suffix}", "close")
            return candle_list[-1].get(key, 0.0)

        # 2. Indicator Value (RSI, EMA, etc.)
        elif source_type == "indicator":
            ind_name = rule.get(f"ind{suffix}", "").lower()
            period = rule.get(f"period{suffix}", 14)

            if ind_name == "rsi":
                return T.rsi(candle_list, period)
            elif ind_name == "ema":
                return T.ema(candle_list, period)
            elif ind_name == "sma":
                return T.sma(candle_list, period)
            elif ind_name == "vwap":
                return T.vwap(candle_list)
            elif ind_name == "supertrend":
                val, trend = T.supertrend(candle_list, period)
                return trend if rule.get("use_trend", False) else val
            elif ind_name == "adx":
                return T.adx(candle_list, period)
            elif ind_name == "bb_upper" or ind_name == "bb_lower":
                mid, upper, lower = T.bollinger_bands(candle_list, period)
                return upper if ind_name == "bb_upper" else lower
            elif ind_name == "atr":
                return T.atr(candle_list, period)

        return 0.0
