# NeoPulse

Automated intraday trading for NSE equities on **Kotak Neo** (Trade API, SDK v3).
A single asyncio process that runs your strategies unattended and recovers by itself from
crashes, restarts, disconnects and broker hiccups.

## What it does

- **Strategies** are small Python classes driven by closed candles (and optionally ticks). The
  same class runs live, in paper mode and in the backtester.
- **Crash-safe order management**: every order is saved before it is sent and tagged with our
  id, so after any crash it is matched to the broker's order book. Fills are derived from the
  broker's cumulative state and never double counted. An order with an unknown outcome is
  reconciled, never blindly re-sent.
- **Recovery on restart**: open orders, positions, strategy state, stop-loss/targets, today's
  P&L and the kill switch are restored from Postgres; positions are reconciled against the
  broker; indicators are warmed up from historical candles.
- **Risk**: per-trade risk sizing, max open positions, entry window, margin check, and a
  kill switch on net daily loss (after statutory charges) that flattens everything.
  Exits are never blocked by risk rules.
- **Intraday hygiene**: no new entries after `ENTRY_CUTOFF`, MIS positions squared off at
  `SQUARE_OFF_TIME` (before the broker's own square-off), daily re-login and instrument sync.
- **Paper mode**: live market data with simulated fills.
- **Charges**: Kotak's Trade API has zero brokerage; STT, exchange, SEBI, stamp duty and GST are
  still computed on every fill and included in P&L.

## Quick start

```bash
make install                 # .venv + dependencies
cp .env.example .env         # fill in Kotak credentials, DB, optional Telegram
make db-up                   # Postgres in Docker (or use your own)
make migrate                 # create tables
make sync                    # download the instrument master (also runs daily)
make run                     # API on :8000 (PAPER_TRADING=True by default)
```

Or run everything in Docker (auto-restarts; recovery runs on every start):

```bash
docker compose up -d --build
```

## Running a strategy

```bash
# available strategies
curl localhost:8000/api/v1/strategies/types

# start MACD_VOLUME on RELIANCE with Rs 1,00,000 allocated, 1% risk per trade, 5-minute candles
curl -X POST localhost:8000/api/v1/strategies -H 'content-type: application/json' -d '{
  "strategy_type": "MACD_VOLUME", "symbol": "RELIANCE", "capital": 100000,
  "timeframe": 5, "risk_per_trade_pct": 0.01, "params": {"ema_period": 200}
}'
```

Instances persist: after a restart every `ACTIVE` instance resumes automatically.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/health` | mode, broker session, feed freshness, kill switch, day P&L, jobs |
| `GET /api/v1/strategies` | instances with position, state, day P&L |
| `POST /api/v1/strategies/{id}/stop` | stop (and by default flatten) an instance |
| `POST /api/v1/strategies/{id}/resume` | resume a stopped/disabled instance |
| `GET /api/v1/orders?days=1` | order log |
| `GET /api/v1/positions` | open positions with live P&L |
| `POST /api/v1/risk/kill-switch` | `{"active": true}` trips it (flattens all); `false` resets |
| `POST /api/v1/risk/flatten-all` | close everything now |

## Backtesting

```bash
make backtest symbol=RELIANCE strategy=MACD_VOLUME interval=5 days=59
.venv/bin/python -m backtest.engine --symbol INFY --strategy RULE_ENGINE --interval 15 \
    --params '{"buy_rules": [...], "sell_rules": [...]}' --plot
```

Orders fill at the next candle's open (no look-ahead), stops gapping through fill at the open,
charges are applied and intraday positions are squared off each session.

## Writing a strategy

Create `app/strategy/library/my_strategy.py`:

```python
from app.strategy import register_strategy
from app.strategy.base import Strategy
from app.strategy.indicators import tb


@register_strategy("EMA_CROSS")
class EmaCross(Strategy):
    """Long when fast EMA crosses above slow EMA."""

    def setup(self):
        self.fast = int(self.params.get("fast", 9))
        self.slow = int(self.params.get("slow", 21))
        self.warmup = self.slow + 2

    async def on_candle(self, candle):
        closes = [c.close for c in self.candles]
        above = tb.ema(closes, self.fast) > tb.ema(closes, self.slow)
        if self.position == 0 and above and not self.ctx.has_pending_order:
            await self.buy(candle.close, stop=candle.close * 0.99, target=candle.close * 1.02, reason="CROSS_UP")
        elif self.position > 0 and not above:
            await self.exit("CROSS_DOWN")
```

`self.state` is persisted (JSON values), stops/targets are enforced on every tick, and
quantity is sized from the instance's capital and risk unless you pass `qty`.

## Tests

```bash
make test   # unit + integration (integration tests use TEST_DATABASE_URL, default
            # postgresql+asyncpg://neopulse_test@localhost:55432/neopulse_test, skipped if unreachable)
```
