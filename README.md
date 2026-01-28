# NeoPulse API ⚡

## Overview

NeoPulse is a production-grade, high-frequency algorithmic trading platform for the Indian Equities market (NSE). It is designed as a **Modular Monolith** using **FastAPI** and **AsyncIO**, capable of handling real-time tick processing with zero-blocking logic.

It features a **"Zombie-Proof" WebSocket feed**, tax-aware risk management, and a unified execution engine that seamlessly switches between **Live Trading (Kotak Neo)** and **Paper Trading (Virtual Broker)**.

## Key Features

* **🛡️ Self-Healing Data Feed:** Detects "Zombie" connections (silent disconnections) and auto-reconnects with exponential backoff.
* **💸 Tax-Aware Risk Engine:** Calculates **Net PnL** (Gross - STT/Exchange Charges) in real-time to trigger Kill Switches *before* capital is eroded.
* **⚡ Async Architecture:** Thread-safe bridging between the blocking Kotak SDK and the main AsyncIO event loop.
* **🧠 Candle-Based Logic:** Strategies enforce "Candle Close" logic to prevent signal repainting.
* **🧊 Smart Iceberg Orders:** Splits large orders into "Legs" to minimize market impact and slippage.
* **🧪 Virtual Broker:** A high-fidelity simulator that mocks order fills, latencies, and partial executions.

## Quick Start

### Prerequisites
* Python 3.10+
* PostgreSQL 15+
* Kotak Neo API Credentials

### Installation

1.  **Clone & Configure**
    ```bash
    git clone https://github.com/sudama011/NeoPulse_api.git
    cd neopulse_api
    cp .env.example .env
    # Edit .env with your Kotak Credentials and PostgreSQL details
    ```

2.  **Initialize Database & Master Data**
    ```bash
    # Create tables
    python scripts/init_db.py

    # Download & Sync Instrument Master (Saves to disk and DB)
    make sync
    ```

3.  **Run the Server**
    ```bash
    # Starts API, Feed, and Strategy Engine
    make run
    ```

## Usage

### 1. Start the Bot (API)
The bot starts in a "Standby" mode. You must activate it via the API.

**POST** `http://localhost:8000/api/v1/engine/start`
```json
{
  "capital": 100000,
  "symbols": ["RELIANCE-EQ", "INFY-EQ"],
  "strategy": "MOMENTUM",
  "max_daily_loss": 2000,
  "risk_per_trade_pct": 0.01,
  "strategy_params": {
    "ema_period": 200
  }
}

```

## Project Structure

```
app/
├── core/           # Circuit Breakers, Global Executors, Loggers
├── data/           # Feed (WebSocket), Stream (Event Bus), Master Data
├── execution/      # Kotak Adapter, Virtual Broker, Order Engine
├── risk/           # Sentinel (Kill Switch), Sizer (Position Sizing)
├── strategy/       # Base Strategy, MACD Logic, Technical Toolbox
└── api/            # FastAPI Endpoints
backtest/           # Backtesting Engine for strategies on historical data
scripts/            # Maintenance scripts (Sync, Backup, Test Feed)
tests/              # Unit & Integration Tests
```