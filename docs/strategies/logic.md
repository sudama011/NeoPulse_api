# Algorithmic Strategies

This document details the quantitative logic deployed in NeoPulse_api.

## 1. Momentum Scalper (RSI + VWAP)
**Objective:** Capture short-term bursts in high-beta stocks.

### Logic
*   **Timeframe:** 1-Minute Candles.
*   **Indicators:**
    *   `RSI(14)`: Relative Strength Index.
    *   `VWAP`: Volume Weighted Average Price (Intraday).
*   **Long Entry Condition:**
    1.  `Close > VWAP` (Trend is Up)
    2.  `RSI` crosses above 60 (Momentum accelerating)
    3.  `Volume > SMA(Volume, 20) * 1.5` (Volume confirmation)
*   **Short Entry Condition:**
    1.  `Close < VWAP`
    2.  `RSI` crosses below 40
*   **Exit:**
    *   Take Profit: 0.5%
    *   Stop Loss: 0.25% (Trailing enabled after 0.2% profit).

## 2. Mean Reversion (Bollinger)
**Objective:** Fade the moves in sideways markets.

### Logic
*   **Timeframe:** 5-Minute Candles.
*   **Indicators:** Bollinger Bands (20, 2).
*   **Logic:**
    *   **Band Squeeze Check:** `(Upper - Lower) / SMA(20) < Threshold` (Ensure not in breakout mode).
    *   **Long:** Price touches Lower Band + Rejection Candle (Hammer).
    *   **Short:** Price touches Upper Band + Rejection Candle (Shooting Star).
*   **Safety:** Do not trade if ADX > 25 (Strong trend indicator).

## 3. Gap Fill Strategy
**Objective:** Trade the "Morning Gap" on Index Futures (NIFTY/BANKNIFTY).

### Logic
*   **Setup (09:15 AM):** Calculate Gap % = `(Open - Prev_Close) / Prev_Close`.
*   **Filter:** Gap must be > 0.3% and < 1.0%.
*   **Execution:**
    *   **Gap Up:** If Gap > 0, place SELL LIMIT order at `Open`. Target = `Prev_Close`.
    *   **Gap Down:** If Gap < 0, place BUY LIMIT order at `Open`. Target = `Prev_Close`.
*   **Time Limit:** Strategy expires at 10:00 AM. If gap not filled, close position.