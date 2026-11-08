# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Personal, single-process asyncio trading system for NSE equities on Kotak Neo (Trade API, SDK v3 `kotakneoapi`, import name `neo_api_client`). FastAPI exposes control endpoints; the real work is background tasks. The top priority is that a strategy runs unattended and that **any crash or restart recovers orders, positions and state from Postgres + the broker**. Kotak charges zero brokerage on API orders; statutory charges are modelled in `app/portfolio/charges.py`.

## Commands

```bash
make install            # .venv + requirements-dev.txt
make migrate            # alembic upgrade head (schema lives in app/models/__init__.py)
make migration msg=...  # autogenerate a migration after model changes
make sync               # download Kotak instrument master
make run                # uvicorn app.main:app
make test               # all tests; integration tests need a Postgres (see below)
make lint / make format # flake8 (.flake8) + mypy / black + isort (line length 120)
.venv/bin/python -m pytest tests/integration/test_trading_flow.py::test_crash_recovery_applies_fills_once
.venv/bin/python -m backtest.engine --symbol RELIANCE --strategy MACD_VOLUME --interval 5 --days 59 [--params JSON] [--plot]
```

Integration tests use a real Postgres at `TEST_DATABASE_URL` (default `postgresql+asyncpg://neopulse_test@localhost:55432/neopulse_test`); the `sf` fixture recreates the schema per test and skips if the DB is unreachable. `tests/fakes.py` has a scriptable `FakeBroker`. Never read `.env`; tests set dummy credentials in `tests/conftest.py`.

## Architecture

`app/container.py` is the composition root: it builds every component with explicit constructor dependencies (no module-level singletons except `settings` and `global_executor`) and owns the startup recovery sequence: load instruments/positions/kill-switch → broker login (retried in background if down) → `OrderManager.recover()` → register active instances → `PositionReconciler.reconcile(startup=True)` (live only) → market feed → `StrategyEngine.start_all()` → supervised periodic loops (`app/core/tasks.py`) and the daily `Scheduler`. API routes get the container from `request.app.state.container`.

Layers (dependencies point downward):
- `app/domain/models.py` — value types (`Side`, `OrderStatus`, `Tick`, `Candle`, `OrderRequest`, `BrokerOrder`, `Fill`, `PositionState`). `PositionState.apply()` is the single source of position/realized-P&L math, shared by live and backtest.
- `app/broker/` — `Broker` port (`base.py`) with `KotakBroker` and `PaperBroker`. `OrderRejected` = definitely not placed; `BrokerUnavailable` = outcome unknown. `KotakSession` owns the SDK client, daily login and auth-retry; all blocking SDK calls go through `session.call()` (thread pool + rate limiter). SDK v3 REST methods return `{"Error": ...}` dicts rather than raising; `response_error()` detects them, and an `Error` holding an SDK validation exception means "not sent".
- `app/market/` — `MarketFeed` (SFeed websocket → `Tick` → `TickBus`, zombie watchdog), `TickBus` (per-subscriber bounded queues + last-price cache used for MTM and paper fills), `CandleBuilder` (session-aligned buckets, cumulative-volume deltas, `flush()` for quiet instruments, `seed()` after warm-up), `HistoryService` (Kotak historical candles), `InstrumentStore`.
- `app/oms/order_manager.py` — order lifecycle. Write-ahead insert, client id sent as broker `tag` (echoed as `GuiOrdId`), fills computed from cumulative `filled_qty` deltas (idempotent across order feed + polling), UNKNOWN orders resolved via tag and never resent, PENDING orders are in-flight and never failed, live orders missing from the book are never assumed dead. Listener callbacks are delivered by a single dispatcher task (ordered, non-reentrant); use `await oms.drain()` in tests.
- `app/portfolio/book.py` — `PositionBook`: per-instance positions + today's realized P&L/charges. `record_fill()` writes fill + order fill state + instance position in ONE transaction.
- `app/risk/` — `RiskManager.check_entry()` gates entries only (kill switch, daily net loss, entry window, max open positions, margin); exits are never blocked. Kill switch is persisted in `kv_store` per day and its flatten callback runs in a separate task.
- `app/strategy/` — `base.py` defines `Strategy` and the `StrategyContext` protocol; `runner.py` (`StrategyRunner`) is the live context; `backtest/engine.py` (`BacktestContext`) is the backtest one. Strategies live in `app/strategy/library/` and register with `@register_strategy("NAME")`; the registry imports that package lazily and import errors are fatal. `StrategyEngine` (`engine.py`) creates/stops/resumes/disables instances, supervises restarts every 30s, and does flatten-all / square-off (including orphaned positions of stopped instances).
- `app/scheduler.py` — daily jobs that run once per trading day inside a time window (session refresh, instrument sync, square-off, EOD report); a failing job retries until its window closes.

Runner invariants worth preserving: every event for an instance (tick, candle timer, fill, order update, flatten) is serialized by `StrategyRunner._lock`; only one working order per instance (`has_pending_order`); stop/target live in persisted `state["stop"]/["target"]` and are enforced on every tick; repeated broker rejections trigger an exponential cooldown; 5 consecutive strategy exceptions disable the instance (flattened, status `DISABLED`). At most one `ACTIVE` instance per instrument (partial unique index), which keeps broker reconciliation unambiguous.

Persistence: `strategy_instance` (config, params, position, JSON state, status), `orders`, `fills`, `kv_store`, `instrument`. Change models in `app/models/__init__.py`, then `make migration msg=...`.

## Conventions

- Prices to the broker: canonical codes only (`nse_cm`, `L/MKT/SL/SL-M`, `DAY`), limit prices rounded to tick size via `InstrumentInfo.round_price`.
- Times are IST-aware (`app/core/clock.py`); market 09:15–15:30, candles aligned to 09:15.
- Optional settings with defaults (risk limits, cutoffs, OMS timings) are in `app/core/settings.py` and documented in `.env.example`.
