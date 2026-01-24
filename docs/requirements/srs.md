# Software Requirements Specification (SRS)

**Project:** NeoPulse_api
**Version:** 1.1.0

## 1. Introduction

This document defines the functional and non-functional requirements for NeoPulse_api. The system is designed to automate intraday trading on the NSE via Kotak Neo API v2, focusing on capital preservation, tax-aware profitability, and operational resilience.

## 2. Functional Requirements (FR)

### 2.1 Authentication & Session Management

* **FR-AUTH-01:** The system MUST perform headless login using Mobile Number, Password, and MPIN.
* **FR-AUTH-02:** The system MUST generate Time-based One-Time Passwords (TOTP) internally using the `pyotp` library without human intervention.
* **FR-AUTH-03:** The system MUST automatically refresh the Session Token every 8 hours or upon receiving an HTTP 401 Unauthorized error.

### 2.2 Market Data & Master Sync

* **FR-DATA-01:** The system MUST subscribe to the WebSocket feed for `LTP`, `OHLC`, and `Volume`.
* **FR-DATA-02:** The system MUST aggregate real-time ticks into 1-minute, 5-minute, and 15-minute OHLCV candles.
* **FR-DATA-03:** The system MUST handle WebSocket disconnections using an exponential backoff strategy (1s, 2s, 4s, 8s).
* **FR-DATA-04 (Scrip Master Drill):** ** The system MUST download the daily "Scrip Master" CSV from Kotak at 08:00 AM, parse it, and update the `instrument_master` database table to map Symbols (e.g., `NIFTY26JANFUT`) to Tokens (e.g., `12345`).

### 2.3 Order Management System (OMS)

* **FR-OMS-01:** The system MUST support `LIMIT`, `MARKET`, `SL-LIMIT`, and `SL-M` order types.
* **FR-OMS-02:** The system MUST implement "Smart Order Slicing" (Iceberg) for orders exceeding the NSE Freeze Quantity (e.g., >1800 for NIFTY) to prevent rejection.
* **FR-OMS-03:** The system MUST track the state of every order (`PENDING` -> `OPEN` -> `TRADED`/`REJECTED`) via WebSocket Order Updates.

### 2.4 Risk Management (NeoSentinel)

* **FR-RISK-01 (Circuit Check):** No Buy order shall be placed within 1% of the Upper Circuit Limit.
* **FR-RISK-02 (Fat Finger):** Any order price deviating >5% from the last 1-minute VWAP MUST be rejected internally.
* **FR-RISK-03 (Net PnL Kill Switch):** ** If daily **Net Realized Loss** (Gross PnL - STT - Taxes) exceeds 2% of allocated capital, the system MUST cancel all pending orders and square off open positions.

### 2.5 Command & Control (Telegram)

* **FR-CMD-01:** ** The system MUST support the following commands:
* `/status`: Health check (API latency, DB connection).
* `/pnl`: Display Net Profit/Loss and Tax estimates.
* `/kill`: Emergency shutdown and square-off.
* `/resume`: Restart trading after a pause.



### 2.6 Simulation & Backtesting

* **FR-SIM-01:** The system MUST provide a `VirtualBroker` mode that intercepts API calls and simulates fills based on live market candles.
* **FR-SIM-02:** The `VirtualBroker` MUST return API responses identical in structure to the production Kotak Neo API.

## 3. Non-Functional Requirements (NFR)

### 3.1 Latency & Performance

* **NFR-PERF-01:** Tick-to-Trade latency (Signal Generation -> API Request Sent) MUST be **< 50 milliseconds**.
* **NFR-PERF-02:** The database MUST handle write throughput of 500 ticks/second without blocking the main event loop.

### 3.2 Reliability & Availability

* **NFR-REL-01:** The system MUST achieve 99.9% uptime during NSE market hours (09:15 to 15:30 IST).
* **NFR-REL-02 (State Reconstruction):** In case of a crash, the system MUST restart and reconstruct its state (Open Positions) from the Database within 30 seconds.
* **NFR-REL-03 (Zombie Check):** ** The system MUST implement an application-level "Heartbeat". If no data is received for 5 seconds (even if TCP is open), it must force a reconnection.

### 3.3 Security

* **NFR-SEC-01:** API Credentials and TOTP Secrets MUST NOT be stored in plaintext source code; they must be loaded from encrypted Environment Variables.
* **NFR-SEC-02:** The Telegram Bot MUST whitelist interactions based on a specific `chat_id`.

### 3.4 Compliance & Data Retention

* **NFR-COMP-01:** The system MUST respect the API Rate Limit of **10 requests per second** using a Token Bucket algorithm. Prioritize `CANCEL` requests over `NEW` orders.
* **NFR-COMP-02:** All orders MUST be tagged with a unique Strategy ID for audit purposes.
* **NFR-COMP-03 (Data Pruning):** ** The system MUST automatically drop market tick data older than 7 days (via Partitioning) to prevent storage overflow.


