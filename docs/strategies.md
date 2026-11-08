# Strategies

All strategies live in `app/strategy/library/` and are registered by name. List them with
`GET /api/v1/strategies/types`. They are examples to build on, not recommendations.

## MACD_VOLUME (long only)

- Entry on a closed candle when the MACD histogram crosses above zero and close > EMA(`ema_period`).
- Stop = close − `atr_stop_mult` × ATR, target = close + `reward_risk` × stop distance.
- Full size when volume > `vol_multiplier` × average volume, half size otherwise.
- Exit when the histogram crosses below zero, or when the stop or target is hit (checked every tick).

Params: `ema_period` (200), `macd_fast` (12), `macd_slow` (26), `macd_signal` (9),
`vol_period` (20), `vol_multiplier` (1.2), `atr_period` (14), `atr_stop_mult` (2.0), `reward_risk` (2.0).

## GRID_ACCUMULATOR (long only, tick driven)

Buys `initial_qty`, then adds `initial_qty + level × qty_step` each time price falls
`grid_step` below the previous level (up to `max_levels`, capped by `max_qty`), and exits
everything once price is `profit_per_share` above the average cost. Grid progress is in
persisted state, so a restart resumes the same grid. It has no stop-loss, so rely on the
account kill switch and square-off.

## RULE_ENGINE (long only)

JSON rules evaluated on each closed candle; all rules in a list must pass.

```json
{
  "warmup": 60,
  "stop_atr_mult": 2,
  "buy_rules":  [{"lhs": {"ind": "ema", "period": 20}, "op": ">", "rhs": {"ind": "ema", "period": 50}},
                 {"lhs": {"ind": "rsi", "period": 14}, "op": "<", "rhs": 60}],
  "sell_rules": [{"lhs": {"ind": "ema", "period": 20}, "op": "<", "rhs": {"ind": "ema", "period": 50}}]
}
```

Indicators: `ema`, `sma`, `rsi`, `atr`, `adx`, `vwap`, `supertrend`, `bb_upper`, `bb_lower`,
`close`, `open`, `high`, `low`. Operators: `>`, `<`, `>=`, `<=`, `==`.

## Sizing

Unless a strategy passes `qty`, quantity = min(capital × risk% × confidence ÷ |entry − stop|,
capital × leverage ÷ entry), rounded down to the lot size. Entries also pass the account risk
checks (kill switch, daily loss, entry window, max open positions, margin).
