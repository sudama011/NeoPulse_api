# NeoPulse Trading Strategies Documentation

## Overview

NeoPulse implements 4 distinct algorithmic trading strategies, each with unique entry/exit logic and risk management profiles.

---

## Strategy Implementations

### 1. 🚀 Momentum Trend Following (`MOMENTUM_TREND`)
**File:** `app/services/strategy/lib/momentum.py`

**Philosophy:** Follow the trend with technical indicators

**Entry Signals:**
- LONG: Price > EMA(50) AND RSI(14) > 60 AND Price > VWAP
- SHORT: Price < EMA(50) AND RSI(14) < 40 AND Price < VWAP

**Exit Targets:**
- Take Profit: +0.9%
- Stop Loss: -0.3%

**Configuration:**
- Indicator Period: EMA(50), RSI(14)
- Cooldown: 10 minutes
- Position Size: Dynamic (risk-based)

**Best For:** Trending markets with clear directional bias

---

### 2. 📊 Gap Fill Strategy (`GAP_FILL`)
**File:** `app/services/strategy/lib/gap_fill.py`

**Philosophy:** Mean revert on intraday gaps

**Entry Signals:**
- LONG: Price < Previous Close AND Price < SMA(20)
- SHORT: Price > Previous Close AND Price > SMA(20)

**Exit Targets:**
- Take Profit: +0.5%
- Stop Loss: -0.4%

**Configuration:**
- Indicator Period: SMA(20)
- Cooldown: 5 minutes
- Position Size: Dynamic (risk-based)

**Best For:** Choppy/mean-reverting markets with gap reversals

---

### 3. 📈 Bollinger Bands Mean Reversion (`MEAN_REVERSION`)
**File:** `app/services/strategy/lib/mean_reversion.py`

**Philosophy:** Exploit overbought/oversold conditions

**Entry Signals:**
- LONG: Price < Lower Bollinger Band AND RSI < 30
- SHORT: Price > Upper Bollinger Band AND RSI > 70

**Exit Targets:**
- Take Profit: +0.6%
- Stop Loss: -0.35%

**Configuration:**
- Bollinger Bands: Period=20, StdDev=2.0
- RSI: Period=14
- Cooldown: 8 minutes
- Position Size: Dynamic (risk-based)

**Best For:** Range-bound markets with extreme reversions

---

### 4. 📍 Opening Range Breakout (`OPENING_RANGE_BREAKOUT`)
**File:** `app/services/strategy/lib/orb.py`

**Philosophy:** Trade breakouts from established morning range

**Setup Phase (9:15 - 9:30 AM IST):**
- Establishes opening range: High, Low, Open from first 15 minutes

**Entry Signals:**
- LONG: Price breaks above (Range High × 1.003)
- SHORT: Price breaks below (Range Low × 0.997)

**Exit Targets:**
- Take Profit: +0.7%
- Stop Loss: -0.4%

**Configuration:**
- Range Duration: 15 minutes
- Breakout Threshold: 0.3% beyond range
- Cooldown: 12 minutes
- Trading Hours: 9:15 AM - 3:15 PM IST
- Position Size: Dynamic (risk-based)

**Best For:** Directional markets with clear opening volatility

---

## Position Sizing

All strategies use the **CapitalManager** for dynamic position sizing:

```
Risk Amount = Capital × Risk%
Risk Per Share = Entry Price - Stop Loss
Position Qty = Risk Amount / Risk Per Share
Position Qty = min(Qty, 50% Capital / Entry Price)  // Max capital check
```

**Default:** 1% risk per trade on available capital

---

## Testing Strategies

### Run All Strategy Tests
```bash
.venv/bin/python research/test_all_strategies.py
```

Tests all strategies with basic initialization checks.

### Run Individual Strategy Tests
```bash
# Test all strategies with backtests
.venv/bin/python research/test_strategies.py --strategy all

# Test specific strategy
.venv/bin/python research/test_strategies.py --strategy momentum

# Test with custom parameters
.venv/bin/python research/test_strategies.py --strategy gap_fill --days 14 --symbol INFY.NS
```

**Available Strategies:**
- `momentum` - Momentum Trend Following
- `gap_fill` - Gap Fill Strategy
- `mean_reversion` - Bollinger Bands
- `orb` - Opening Range Breakout
- `all` - All strategies (default)

---

## Strategy Selection Guide

| Market Condition | Best Strategy | Why |
|---|---|---|
| Strong Trend | Momentum Trend | Follows directional momentum |
| Choppy/Range-bound | Gap Fill | Exploits reversions |
| Extreme Moves | Mean Reversion | Catches overbought/oversold |
| Morning Volatility | ORB | Capitalizes on opening breakout |
| Mixed | Momentum | Most robust across conditions |

---

## Risk Management Features

All strategies implement:

✅ **Dynamic Position Sizing**
- Scales position based on capital and risk tolerance
- Prevents over-leveraging

✅ **Stop Loss / Take Profit**
- Predefined exit levels for every trade
- Automatic position closure

✅ **Cooldown Periods**
- Prevents rapid re-entry after exits
- Reduces whipsaw risk

✅ **Trade Slot Management**
- Limits concurrent open positions (default: 3)
- Prevents excessive leverage

✅ **Daily Loss Limits**
- Stops trading after max loss reached
- Capital preservation

---

## Performance Optimization

### Thread-Safe Indicator Calculations
Indicator calculations run in thread pool via `run_blocking()`:
- Pandas operations don't block event loop
- Allows responsive market data processing

### Async Order Execution
All orders execute asynchronously:
- Non-blocking broker communication
- Efficient resource utilization

### Lock-Free Updates
Uses asyncio.Lock for atomic state updates:
- Safe concurrent access to position data
- No race conditions

---

## Integrating a New Strategy

1. **Create Strategy Class** inheriting from `BaseStrategy`:
```python
from app.services.strategy.base import BaseStrategy

class MyStrategy(BaseStrategy):
    def __init__(self, symbol: str, token: str, risk_monitor=None, capital_manager=None):
        super().__init__("MY_STRATEGY", symbol, token, risk_monitor)
        self.capital_manager = capital_manager or CapitalManager(...)
    
    async def on_candle_close(self, candle: dict):
        # Your entry/exit logic here
        pass
```

2. **Register in Manager**:
```python
# app/services/strategy/manager.py
STRATEGY_MAP = {
    "MY_STRATEGY": MyStrategy,
}
```

3. **Add Test Configuration**:
```python
# research/test_strategies.py
STRATEGY_CONFIGS = {
    "my_strategy": {
        "display_name": "My Strategy",
        "symbol": "RELIANCE.NS",
        "days": 7,
        ...
    }
}
```

---

## Configuration via API

Start a strategy via REST API:

```bash
curl -X POST http://localhost:8000/api/v1/engine/start \
  -H "Content-Type: application/json" \
  -d '{
    "capital": 100000,
    "symbols": ["RELIANCE", "TCS", "INFY"],
    "strategy": "MOMENTUM_TREND",
    "leverage": 1.0,
    "max_daily_loss": 1000,
    "max_concurrent_trades": 3,
    "strategy_params": {
      "risk_per_trade_pct": 0.01
    }
  }'
```

**Available Strategies:**
- `MOMENTUM_TREND`
- `GAP_FILL`
- `MEAN_REVERSION`
- `OPENING_RANGE_BREAKOUT`

---

## Monitoring & Debugging

### View Active Strategies
```bash
curl http://localhost:8000/api/v1/engine/status
```

### Logs
Check strategy logs:
```bash
tail -f logs/neopulse.log | grep "MomentumStrategy"
tail -f logs/neopulse.log | grep "GapFillStrategy"
```

### Trade Tracking
Monitor individual trades:
```bash
.venv/bin/python -c "
import asyncio
from app.db.session import AsyncSessionLocal
from app.models.orders import OrderLedger
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(OrderLedger).limit(10))
        for order in result.scalars():
            print(f'{order.symbol}: {order.side} {order.qty} @ {order.price}')

asyncio.run(main())
"
```

---

## Future Enhancements

- [ ] Machine Learning strategy with feature engineering
- [ ] Multi-timeframe analysis (5m + 15m + 1h)
- [ ] Sentiment analysis integration
- [ ] Portfolio optimization across strategies
- [ ] Advanced risk metrics (Sharpe, Sortino)
- [ ] Strategy ensemble voting

---

## Support

For issues or questions:
1. Check logs in `logs/`
2. Review strategy parameters
3. Backtest with different market conditions
4. Optimize stop loss/take profit ratios

---

**Last Updated:** 25 January 2026  
**Version:** 1.0.0
