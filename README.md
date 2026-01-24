# NeoPulse_api ⚡

## Overview

NeoPulse_api is a high-frequency, asynchronous trading bot engineered for the Indian Equities and Derivatives market. It leverages the **Kotak Securities Neo API v2** to execute zero-brokerage intraday strategies.

The system is built on a **Modular Monolith** architecture using **FastAPI** for the event loop, **PostgreSQL** for persistence, and **Telegram** for command-and-control. It features a "Virtual Broker" for live paper trading and a unified backtesting engine.

## Key Features

* **🚀 Ultra-Low Latency:** AsyncIO-driven core with non-blocking WebSocket ingestion.
* **🛡️ NeoSentinel Risk Engine:** Pre-trade checks for circuit limits, fat-finger errors, and max drawdown.
* **🧪 Virtual Broker:** Live simulation mode that mimics Kotak API responses 1:1.
* **🧠 Hybrid Strategy Engine:** Supports Momentum, Mean Reversion, and Iceberg execution.
* **📱 Telegram Ops:** Full control via chat (PnL monitoring, Kill Switch, Signal Approvals).

## Quick Start

### Prerequisites

* Python 3.10+
* PostgreSQL 14+ (with TimescaleDB recommended)
* Kotak Neo API Credentials

### Installation

1. **Clone the Repository**
```bash
git clone [https://github.com/your-org/neopulse_api.git](https://www.google.com/search?q=https://github.com/your-org/neopulse_api.git)
cd neopulse_api
```

2. **Environment Setup**
```bash
cp.env.example.env
# Edit.env with your CONSUMER_KEY, SECRET, and MPIN
```

3. **Run with Docker**
```bash
docker-compose up -d --build
```

## Documentation

Full documentation is available in the `docs/` folder.

* (docs/architecture/system_design.md)
* (docs/strategies/logic.md)
* [User Manual](docs/operations/manual.md)

## Project Structure

```
NeoPulse_api/
├── app/                        # The Application Core
│   ├── __init__.py
│   ├── main.py                 # FastAPI Entry Point (The "Brain")
│   │
│   ├── core/                   # ⚙️ Infrastructure & Config
│   │   ├── __init__.py
│   │   ├── settings.py           # Pydantic Settings (Loads .env)
│   │   ├── security.py         # Encryption & JWT Handling
│   │   ├── logger.py          # Custom Log Formatter (JSON/Text)
│   │   └── events.py           # Global Event Bus (Asyncio Queue)
│   │
│   ├── db/                     # 💾 Database Infrastructure
│   │   ├── __init__.py
│   │   ├── session.py          # Async Session Factory (get_db)
│   │   └── base.py             # Imports all models (for Alembic auto-generation)
│   │
│   ├── models/                 # 🗄️ SQLAlchemy Models (The "Truth")
│   │   ├── __init__.py
│   │   ├── base.py             # BaseModel
│   │   ├── users.py            # User & Auth tables
│   │   ├── market_data.py      # Instrument Master & Ticks
│   │   └── orders.py           # Ledger, TradeBook, Strategy Config
│   │
│   ├── schemas/                # 📝 Pydantic Schemas (Data Validation)
│   │   ├── __init__.py
│   │   ├── common.py           # Shared Enums/Base Models
│   │   ├── requests.py         # Input validation (e.g., PlaceOrderRequest)
│   │   └── responses.py        # Output formatting (e.g., PnLReport)
│   │
│   ├── adapters/               # 🔌 External Integrations
│   │   ├── kotak/
│   │   │   ├── auth.py         # Login & Token Management
│   │   │   ├── rest.py         # API Wrapper (Orders, Positions)
│   │   │   └── socket.py       # WebSocket Client
│   │   ├── telegram/
│   │   │   ├── bot.py          # Bot Lifecycle Manager
│   │   │   └── handlers.py     # Command Logic (/status, /kill)
│   │   └── virtual/            # "Virtual Broker" Mock Implementation
│   │
│   ├── modules/                # 🧠 Business Logic (The "Monolith")
│   │   ├── ingestion/          # Ticker -> Candle Aggregation
│   │   │   ├── processor.py
│   │   │   └── drill.py        # Morning Master Sync Logic
│   │   ├── strategy/
│   │   │   ├── engine.py       # Strategy Runner
│   │   │   └── lib/            # Specific Strategy Logic
│   │   │       ├── momentum.py
│   │   │       └── gap_fill.py
│   │   ├── oms/                # Order Management System
│   │   │   ├── router.py       # Routes to Real or Virtual Broker
│   │   │   └── iceberg.py      # Order Slicing Logic
│   │   └── risk/               # NeoSentinel
│   │       └── checks.py       # Fat-finger & Circuit limits
│   │
│   └── api/                    # 🌐 Web API (Future Dashboard)
│       └── v1/
│           ├── deps.py         # Dependency Injection (Current User)
│           └── routers/
│               ├── auth.py
│               ├── dashboard.py
│               └── webhooks.py # For TradingView alerts (optional)
│
├── db/                         # 🛠️ Database Migrations
│   ├── migrations/             # Alembic versions (SQL scripts)
│   └── alembic.ini             # Migration Config
│
├── docs/                       # 📚 Documentation
│   ├── architecture/
│   ├── api/
│   └── setup.md
│
├── scripts/                    # 🤖 Operational Scripts
│   ├── init_db.py              # Create tables
│   ├── morning_drill.py        # Cron job: Sync Master
│   └── backfill_data.py        # Import historical data
|   └── backup_db.sh            # Backup script
|
├── tests/                      # 🧪 Testing
│   ├── conftest.py             # Fixtures (DB, Event Loop)
│   ├── unit/                   # Fast logic tests
│   └── integration/            # Full flow tests (Mocked Kotak)
│   └── backtest/               # Backtesting
│
├── .env                        # Secrets (NEVER COMMIT)
├── .gitignore
├── docker-compose.yml
└── requirements.txt
|
|__ Makefile
|__ README.md
|__ pyproject.toml
|__ .env.example

```