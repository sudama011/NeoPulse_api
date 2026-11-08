# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

NeoPulse is an async algorithmic trading backend for NSE equities: a FastAPI modular monolith that consumes the Kotak Neo WebSocket feed, runs strategies per symbol, applies risk checks, and routes orders to either the live Kotak broker or an in-process virtual broker. A separate `backtest/` package replays historical data (via yfinance) through the same strategy classes.

## Commands

The Makefile uses `.venv/bin/*` directly, so a venv at `.venv` is assumed.

```bash
make install                     # create .venv, install requirements.txt (dev tools: pip install -r requirements-dev.txt)
make docker-up                   # Postgres 15 via docker-compose (reads .env)
make db-init                     # scripts/init_db.py — DROPS and recreates all tables
make db-migrate msg='desc'       # alembic autogenerate; make db-upgrade / db-downgrade
make sync                        # scripts/sync_master.py — download Kotak instrument master to disk + DB
make run                         # uvicorn app.main:app --reload on :8000

make test                        # pytest tests/ -v
.venv/bin/python -m pytest tests/unit/strategy/test_base.py::TestName::test_name   # single test
make lint                        # flake8 (E501 ignored) + mypy over app backtest tests scripts
make format                      # black + isort (line length 120)

# Backtest (run from repo root)
.venv/bin/python backtest/engine.py --symbol HDFCBANK --strategy MACD_VOLUME --days 59 --interval 5m --capital 100000 [--interactive]
```

pytest runs with `asyncio_mode = "auto"`, so `async def test_*` needs no decorator. `tests/conftest.py` provides `mock_broker` and `mock_db_session` fixtures; tests don't need a live DB or broker.

Config comes from `.env` (see `.env.example`) via `app/core/settings.py`. `PAPER_TRADING=True` selects the virtual broker.

## Architecture

### Module-level singletons
Every major component is a global instance created at import time and shared across the app: `market_feed`, `data_stream`, `master_data`, `execution_engine`, `risk_manager`, `strategy_engine`, `global_executor`, `kotak_adapter`, `virtual_broker`. Code imports and calls these directly rather than using dependency injection. Tests and the backtester **monkey-patch these singletons**. For example, `BacktestEngine.run()` swaps `execution_engine.broker` and replaces `risk_manager.can_trade` and `calculate_size`, then restores them. Keep that in mind when changing method signatures on these objects.

`ExecutionEngine.broker` is chosen once at construction (`virtual_broker if settings.PAPER_TRADING else kotak_adapter`). Both implement `BrokerAdapter` in `app/execution/core.py`.

### Startup order (`app/main.py` lifespan)
global thread pool → master data cache → execution engine login → risk manager (loads the active `TradingSession` from the DB) → `data_stream.consume()` task → `market_feed.connect()` task → strategy engine loads active `StrategyInstance` rows. Trading itself starts only when `POST /api/v1/engine/start` is called. That endpoint creates or reuses a `TradingSession`, inserts `StrategyInstance` rows, then re-runs `risk_manager.initialize()` and `strategy_engine.initialize()`/`start()`. `POST /engine/stop` deactivates them.

### Kotak SDK (v3, `kotakneoapi`)
The broker SDK is `kotakneoapi` 3.x. Its import name is still `neo_api_client`. Don't install it alongside the legacy `neo-api-client` package, because both write into the same folder. `app/execution/kotak.py` is the only place that should know the SDK's response shapes:
- v3 REST methods return `{"Error"|"error"|"Error Message": ...}` dicts instead of raising. `_sdk_error()` detects these, and `place_order` turns them into `{"stat": "Not_Ok", "errMsg": ...}`, which `ExecutionEngine` parses.
- `positions()` returns raw rows (`flBuyQty`, `buyAmt`, `tok`, …). `get_positions()` adds `netQty` / `avgPrice` / `realizedPNL` / `instrumentToken` / `instrumentName` for the sentinel and `BaseStrategy.sync_position`. `get_limits()` adds a lowercase `net` key.
- v3 validates orders on the client side. It accepts only canonical codes: segment `nse_cm`…, order_type `L/MKT/SL/SL-M`, validity `DAY/IOC`, and `price > 0` for `L`/`SL`.
- The SDK writes its own log file to `logs/neo-api-client.log` (`NEO_LOG_*` env vars).

### Tick flow
1. `MarketFeed.connect()` (`app/data/feed.py`) opens the v3 async SFeed socket via `kotak_adapter.client.create_websocket()`. It needs a logged-in trade session, so the feed waits for `kotak_adapter.is_logged_in`. `_read_loop` iterates typed `SFeedScrip` messages and turns them into the legacy tick dict (`tk`, `ltp`, `v` = cumulative day volume, `ltq`, `ltt`, `op`/`h`/`lo`/`c`) with `normalize_message()`. Everything downstream depends on those keys.
2. `DataStream` is an in-process pub/sub. One bounded main queue (10k) feeds per-subscriber queues (1k) keyed by instrument token (`tick["tk"]`). When a queue is full, ticks are dropped and counted in `metrics`; publishers never block.
3. `StrategyEngine._run_strategy_loop` runs one asyncio task per strategy. Each task subscribes to its token and calls `strategy.safe_on_tick(tick)`.
4. SDK REST calls (place order, positions, limits) are blocking and must go through `await run_blocking(fn, ...)` (`app/core/executors.py`), which uses the global `ThreadPoolExecutor`. The WebSocket is native async and runs on the main loop.
5. The SDK reconnects dropped connections on its own. On top of that, `MarketFeed.connect()` runs a watchdog. If subscribed tokens get no packets for 10 seconds, or the SDK gives up reconnecting, it tears down the socket and rebuilds it with exponential backoff (up to 60 seconds). `market_feed.subscribe()` queues tokens while disconnected and sends them on connect.

### Strategies
- **Registry pattern:** `app/strategy/__init__.py` auto-imports every module in `app/strategy/` at package import time. Classes decorated with `@register_strategy("NAME")` become available by name (case-insensitive) to the API (`strategy_type`), the DB (`StrategyInstance.strategy_type`), and the backtester (`--strategy`). One class can have several aliases. For example, `MACD_VOLUME` and `MOMENTUM` both map to `MACDVolumeStrategy`. To add a strategy, drop a new module into `app/strategy/`. An import error there is logged, not raised, so a broken strategy silently disappears from the registry.
- **`BaseStrategy` (`base.py`):** subclasses implement `on_tick`, or override `on_candle` for candle-close logic. The backtester calls `safe_on_candle`. The default `on_candle` appends to `self.candles` and converts the candle into a tick-like dict. Always trade through `self.buy()` / `self.sell()`. These infer entry vs exit from the current `self.position`, ask `risk_manager.calculate_size` for entry quantity when `qty` is omitted, debounce for 1 second, and update position/avg price from the fill. `safe_on_*` disables a strategy after 5 consecutive exceptions. `initialize()` reconciles position from the broker.
- `StrategyEngine.initialize()` builds strategies with `strategy_cls(name=, symbol=, token=, params={}, leverage=)`, so strategy constructors must accept `leverage`. Strategies are keyed by **token**, which means only one active strategy per instrument.
- `GenericStrategy` (`GENERIC` / `RULE_ENGINE`) is a JSON rule engine over `Toolbox` indicators (`toolbox.py`).

### Execution and risk
- `ExecutionEngine.execute_order` runs in this order: `risk_manager.can_trade` → look up `freeze_qty`/segment in master data → if quantity exceeds `freeze_qty`, split into iceberg legs → send via `broker` → persist to `OrderLedger`.
- `RiskManager` combines `RiskSentinel` (daily-loss kill switch, concurrent-trade slots, broker state sync) and `PositionSizer`. `can_trade` returns False until `initialize()` has run. Per-strategy entry checks in `BaseStrategy.buy/sell` are currently stubbed to `allowed = True`, so the engine-level `can_trade` is the real gate.

### Data / DB
- Async SQLAlchemy plus asyncpg (`app/db/session.py` provides `AsyncSessionLocal` and `engine`). Models live in `app/models/`, and `app/models/__init__.py` imports them all for Alembic/`create_all`. `alembic/versions/` is currently empty; the schema is created by `make db-init`, which drops existing tables first.
- `master_data` holds an in-RAM symbol↔token cache backed by `InstrumentMaster` and `instruments_master.csv` (generated by `make sync`). API request validation (`StrategyConfig.symbol`) rejects symbols missing from this cache.

### Docs
`docs/system_design.md` (architecture diagram, concurrency model), `docs/strategies.md` (MACD/momentum strategy logic), `docs/srs.md` (requirements). The README's sample `/engine/start` payload is outdated. The current schema is `StartRequest` in `app/schemas/requests.py`: session-level `capital` / `max_daily_loss` / `max_concurrent_trades` plus a `strategies: [{symbol, strategy_type, leverage, sizing_method, risk_per_trade_pct}]` list.
