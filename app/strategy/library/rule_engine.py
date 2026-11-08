import operator
from typing import Any

from app.domain.models import Candle
from app.strategy import register_strategy
from app.strategy.base import Strategy
from app.strategy.indicators import tb

_OPS = {">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le, "==": operator.eq}


@register_strategy("RULE_ENGINE")
class RuleEngineStrategy(Strategy):
    """Long-only, configured by JSON rules (all rules must pass, i.e. AND).

    Params:
      buy_rules / sell_rules: list of rules, each
        {"lhs": <operand>, "op": ">", "rhs": <operand>}
      where an operand is a number, or {"ind": "ema"|"sma"|"rsi"|"atr"|"adx"|"vwap"|"supertrend"|
        "bb_upper"|"bb_lower"|"close"|"open"|"high"|"low", "period": 14}
      stop_atr_mult (optional): ATR multiple for a protective stop on entry.
      warmup (default 50)
    Example: {"buy_rules": [{"lhs": {"ind": "ema", "period": 20}, "op": ">", "rhs": {"ind": "ema", "period": 50}}],
              "sell_rules": [{"lhs": {"ind": "rsi", "period": 14}, "op": ">", "rhs": 70}]}
    """

    def setup(self) -> None:
        self.buy_rules = self.params.get("buy_rules", [])
        self.sell_rules = self.params.get("sell_rules", [])
        self.stop_atr_mult = self.params.get("stop_atr_mult")
        self.warmup = int(self.params.get("warmup", 50))
        for rule in self.buy_rules + self.sell_rules:
            if rule.get("op") not in _OPS:
                raise ValueError(f"Invalid operator in rule {rule}")

    async def on_candle(self, candle: Candle) -> None:
        if self.ctx.has_pending_order:
            return
        if self.position > 0 and self._all(self.sell_rules):
            await self.exit("RULES_EXIT")
        elif self.position == 0 and self._all(self.buy_rules):
            stop = None
            if self.stop_atr_mult:
                atr = tb.atr([c.to_dict() for c in self.candles], 14)
                stop = candle.close - atr * float(self.stop_atr_mult) if atr else None
            await self.buy(candle.close, stop=stop, reason="RULES_ENTRY")

    def _all(self, rules: list[dict]) -> bool:
        return bool(rules) and all(_OPS[r["op"]](self._value(r["lhs"]), self._value(r["rhs"])) for r in rules)

    def _value(self, operand: Any) -> float:
        if isinstance(operand, (int, float)):
            return float(operand)
        ind = str(operand.get("ind", "")).lower()
        period = int(operand.get("period", 14))
        last = self.candles[-1]
        if ind in ("close", "open", "high", "low"):
            return float(getattr(last, ind))

        closes = [c.close for c in self.candles]
        bars = [c.to_dict() for c in self.candles]
        match ind:
            case "ema":
                return tb.ema(closes, period)
            case "sma":
                return tb.sma(closes, period)
            case "rsi":
                return tb.rsi(closes, period)
            case "atr":
                return tb.atr(bars, period)
            case "adx":
                return tb.adx(bars, period)
            case "vwap":
                return tb.vwap(bars)
            case "supertrend":
                return tb.supertrend(bars, period)[0]
            case "bb_upper":
                return tb.bollinger_bands(closes, period)[1]
            case "bb_lower":
                return tb.bollinger_bands(closes, period)[2]
        raise ValueError(f"Unknown indicator '{ind}'")
