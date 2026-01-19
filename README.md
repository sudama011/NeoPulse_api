# NeoPulse_api

**NeoPulse_api** is a production-grade, event-driven intraday algorithmic trading bot specifically engineered for the **Kotak Securities Neo API**. Designed for the NSE (National Stock Exchange), it prioritizes capital preservation through a multi-layered **NeoSentinel Risk Shield** while executing high-precision strategies like Opening Range Breakout (ORB).

## 🚀 Key Features

* **Modular Architecture:** Decoupled components for Auth, Risk, Strategy, and Execution for easy extensibility.
* **NeoSentinel Risk Shield:** * **Global Kill-Switch:** Instantly halts all trading activity if limits are breached.
* **Daily Loss Protection:** Hard-stop on MTM losses.
* **Auto Square-Off:** Mandatory intraday exit at 3:10 PM.


* **Event-Driven Engine:** Real-time WebSocket ingestion for tick-by-tick processing.
* **Friction-Aware Logic:** Integrated Tax Calculator tuned for Kotak Neo's zero-brokerage plan (~0.04% statutory taxes).
* **State Recovery:** Intelligent reconciliation logic to resume operations and track positions after system restarts or crashes.

## 🛠 Tech Stack

* **Core:** Python 3.10+
* **API Client:** `neo-api-client` (Official Kotak Neo SDK)
* **Web Framework:** FastAPI (Monitoring Dashboard & Webhooks)
* **Data Science:** Pandas & NumPy
* **Persistence:** PostgreSQL (Trade logs & Performance tracking)
* **Validation:** Pydantic v2

---

## 📂 Project Structure

```text
app/
├── api/             # FastAPI routes for monitoring & control
├── core/            # App configuration & settings
├── db/              # Database models & session management
├── engine/          # The "Trading Brain" (Auth, Data, Execution, Risk)
├── models/          # Shared Pydantic/Domain models
├── repositories/    # Data access layer for trades & state
├── services/        # Business logic orchestration
├── strategies/      # Strategy implementations (e.g., ORB)
└── utils/           # Time & Math helpers

```

---

## ⚙️ Setup & Installation

### 1. Prerequisites

* Oracle Cloud / Ubuntu Linux Instance.
* Python 3.10 or higher.
* PostgreSQL Database.

### 2. Installation

```bash
# Clone the repository
git clone https://github.com/your-username/NeoPulse_api.git
cd NeoPulse_api

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

```

### 3. Environment Configuration

Create a `.env` file in the root directory:

```env
NEO_API_KEY=your_key
NEO_API_SECRET=your_secret
NEO_CONSUMER_KEY=your_consumer_key
NEO_CONSUMER_SECRET=your_consumer_secret
NEO_PASSWORD=your_password
NEO_TOTP_SEED=your_totp_seed
DATABASE_URL=postgresql://user:password@localhost/neopulse

```

---

## 📈 Strategic Logic: ORB

The bot currently ships with the **Opening Range Breakout (ORB)** strategy.

* **Range Definition:** 09:15 AM - 09:30 AM.
* **Execution:** SL-Limit orders placed at range high/low.
* **Breakeven Calculation:** Uses the `TaxCalculator` to ensure target prices cover statutory costs:



---

## ⚠️ Disclaimer

Trading in equities and derivatives involves substantial risk. **NeoPulse_api** is provided as-is for educational and research purposes. The developers are not responsible for financial losses incurred through the use of this software. Always test in a paper-trading/sandbox environment before deploying real capital.
