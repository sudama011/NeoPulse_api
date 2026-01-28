# Algorithmic Strategies

This document details the quantitative logic currently deployed in `app/strategy/strategies.py`.

## 1. MACD Volume Trend (The "MOMENTUM" Strategy)

**Objective:** Capture significant intraday trends by combining Momentum (MACD) with Trend Filters (EMA) and Volatility Sizing (ATR).

### A. Configuration
* **Timeframe:** 1-Minute Candles (Constructed from live ticks).
* **Repainting Protection:** Signals are ONLY generated on **Candle Close** (when the minute changes).

### B. Indicators
1.  **Trend Filter:** `EMA(200)` (Exponential Moving Average).
2.  **Momentum:** `MACD(12, 26, 9)`.
3.  **Volatility:** `ATR(14)` (Average True Range) for dynamic Stop Loss.

### C. Entry Logic (Long)
A trade is initiated if **ALL** conditions are met:
1.  **Trend Alignment:** Close Price > EMA(200).
2.  **Momentum Trigger:** MACD Histogram Cross-Over (Yesterday < 0 AND Today > 0).
3.  **Risk Check:** Account is not in "Kill Switch" mode.

### D. Exit Logic
1.  **Technical Exit:** MACD Histogram Cross-Under (Momentum loss).
2.  **Stop Loss:** `Entry Price - (2 * ATR)`.
    * *Note:* The SL is monitored on *every tick* (Fast Exit), not just candle close.

### E. Position Sizing
Quantity is calculated dynamically based on risk:
$$
Qty = \frac{\text{Total Capital} \times \text{Risk \% per Trade}}{\text{Entry Price} - \text{Stop Loss Price}}
$$
