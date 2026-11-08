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

## ORB — Opening Range Breakout (intraday, long + short)

Based on the "stocks in play" opening-range breakout studied by Zarattini & Aziz (2023).
- The first `range_bars` candles define the opening range.
- Only symbols whose opening volume is at least `min_rvol` × their 14-session average trade.
- The first close beyond the range in the direction of the opening candle enters at market.
- Stop: the other side of the range, capped at `max_stop_pct` of price. Target: `reward_risk` × risk.
- At most one trade per symbol per day, and everything is flat by square-off (15:15).
- On restart it replays about 20 sessions of history, so the volume baseline is ready immediately.

Run it on a basket: one instance per stock (`POST /api/v1/strategies/bulk`), each with
`capital_per_symbol = account capital / MAX_OPEN_POSITIONS`. The risk manager then caps
concurrency exactly as the portfolio backtester does.

## Research log (November 2026)

Target asked for: CAGR ≥ 20%, Sharpe > 3, max drawdown < 15%. Rules were fixed before
testing, not tuned to the data. Costs: zero brokerage plus full statutory charges, 2 bps
slippage, market orders filled at the next bar's open, and intraday square-off at 15:15.
Universe: 40 current Nifty-50 stocks. Today's constituents create survivorship bias, which
makes long-only results look better than reality. Capital: ₹10L, 5 concurrent positions.

| Strategy | Data | CAGR | Sharpe | Max DD | Verdict |
|---|---|---|---|---|---|
| RSI2_PULLBACK (CNC) | daily 2015–2026 | −6.3% | −0.34 | −54% | fails: delivery STT eats a small gross edge |
| DONCHIAN_TREND (CNC) | daily 2015–2026 | +1.9% | 0.20 | −35% | fails; negative out-of-sample (2022+) |
| IBS_REVERSION (CNC) | daily 2015–2026 | −0.5% | 0.03 | −38% | fails; negative gross on NIFTYBEES/BANKBEES |
| ORB (MIS) | 60-min bars, 2 years | −1.8% | −0.73 | −5.7% | hourly bars are too coarse for a breakout |
| **ORB (MIS)** | **15-min bars, 56 days** | 70%* | 7.4* | −0.8% | **promising but unproven** |
| ORB (MIS) | 5-min bars, 56 days | 53%* | 5.9* | −1.7% | same caveat |

\* Annualised from 56 trading days and about 110 trades. Over a window this short, Sharpe has
a standard error of roughly ±2.5, and the result was uneven: Sharpe about 2.7 in the first
half and about 10 in the second. It also stayed positive across reward/risk settings and
direction modes, and the relative-volume filter is what made it work. That matches the
published research, but it is not proof.

**Conclusion:** no strategy here is validated to meet the target. A Sharpe above 3, sustained
for years, is extraordinarily rare even for professional funds; treat any backtest that shows
it with suspicion. ORB is the candidate worth validating:

1. `python -m backtest.portfolio --strategy ORB --interval 15 --days 365 --product MIS --source kotak --split <date>`
   (longer Kotak history, using your credentials and the instrument master).
2. Paper trade it (`PAPER_TRADING=True`) for at least 4–6 weeks and compare with the backtest.
3. Go live with small capital first.
