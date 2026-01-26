# Strategy Configurations for Backtesting

BACKTEST_CONFIG = {
    "capital": 100000.0,
    "days": 5,  # Number of days to backtest
    "symbols": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "ADANIENT.NS"],
    # Strategy specific overrides
    "strategies": {
        "MOMENTUM": {
            "rsi_period": 14,
            "ema_period": 50,
            "stop_loss_pct": 0.0030,  # 0.3%
            "take_profit_pct": 0.0090,  # 0.9%
        },
        "GAP_FILL": {"sma_period": 20, "cooldown_minutes": 5},
        "MEAN_REVERSION": {"bb_period": 20, "rsi_period": 14},
        "ORB": {"range_minutes": 15, "breakout_threshold": 0.003},
    },
}
