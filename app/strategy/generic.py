import operator
from app.strategy.base import BaseStrategy, RiskManager
from app.strategy.toolbox import Toolbox as T

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
    OPS = {
        '>': operator.gt,
        '<': operator.lt,
        '>=': operator.ge,
        '<=': operator.le,
        '==': operator.eq
    }

    def __init__(self, symbol: str, token: str, risk_manager: RiskManager, config: dict):
        """
        config example:
        {
            "name": "GoldenCross",
            "buy_rules": [
                {"type": "indicator_cross", "ind1": "ema", "p1": 20, "op": ">", "ind2": "ema", "p2": 50},
                {"type": "value_check", "ind": "rsi", "period": 14, "op": ">", "val": 50}
            ],
            "sell_rules": [...]
        }
        """
        name = config.get("name", "GENERIC")
        super().__init__(name, symbol, token, risk_manager)
        self.config = config

    async def logic(self, candle: dict):
        # We need enough data to calculate indicators
        if len(self.candles) < 50: 
            return

        close = candle['close']

        # 1. Check BUY Conditions (All must be True - 'AND' logic)
        if self.position == 0:
            if self._evaluate_rules(self.config.get('buy_rules', []), candle):
                await self.execute_order("BUY", close, "GENERIC_BUY")

        # 2. Check SELL/EXIT Conditions
        elif self.position > 0:
            # Check Stop Loss / Take Profit Rules first (if defined in config)
            # Otherwise, check technical exit rules
            if self._evaluate_rules(self.config.get('sell_rules', []), candle):
                await self.execute_order("SELL", close, "GENERIC_EXIT")

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
            op_func = self.OPS.get(rule.get('op', '>'))
            
            # --- FETCH LEFT SIDE VALUE ---
            val1 = self._get_value(rule.get('lhs_type', 'indicator'), rule, '1')
            
            # --- FETCH RIGHT SIDE VALUE ---
            # Can be a static value (30, 50, 0) or another indicator
            if 'val' in rule:
                val2 = rule['val']
            else:
                val2 = self._get_value(rule.get('rhs_type', 'indicator'), rule, '2')

            return op_func(val1, val2)

        except Exception as e:
            self.logger.error(f"Rule Eval Error: {e} | Rule: {rule}")
            return False

    def _get_value(self, source_type: str, rule: dict, suffix: str = '') -> float:
        """Helper to fetch values dynamically from Toolbox or Candle."""
        
        # 1. Price Value (Close, Open, High, Low)
        if source_type == 'price':
            key = rule.get(f'price_key{suffix}', 'close')
            return self.candles[-1].get(key, 0.0)

        # 2. Indicator Value (RSI, EMA, etc.)
        elif source_type == 'indicator':
            ind_name = rule.get(f'ind{suffix}', '').lower()
            period = rule.get(f'period{suffix}', 14)
            
            # Map strings to Toolbox functions
            if ind_name == 'rsi':
                return T.rsi(self.candles, period)
            elif ind_name == 'ema':
                return T.ema(self.candles, period)
            elif ind_name == 'sma':
                return T.sma(self.candles, period)
            elif ind_name == 'vwap':
                return T.vwap(self.candles)
            elif ind_name == 'supertrend':
                # Supertrend returns (value, trend), we usually compare trend or value
                val, trend = T.supertrend(self.candles, period)
                return trend if rule.get('use_trend', False) else val
                
        return 0.0