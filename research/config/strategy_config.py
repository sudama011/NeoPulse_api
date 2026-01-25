"""
Strategy Configuration - Maps strategies to symbols for testing
Defines which strategy runs on which stock with custom parameters
"""

STRATEGY_CONFIG = {
    # MOMENTUM_TREND Strategy
    "MOMENTUM_TREND": {
        "stocks": ["RELIANCE", "TCS", "INFY", "HDFC", "ICICIBANK"],
        "parameters": {
            "rsi_period": 14,
            "ema_period": 50,
            "vwap_period": 14,
            "stop_loss_pct": 0.003,
            "take_profit_pct": 0.009,
            "cooldown_minutes": 10,
        }
    },
    
    # GAP_FILL Strategy
    "GAP_FILL": {
        "stocks": ["WIPRO", "BAJAJFINSV", "LANDT", "SBIN", "MARUTI"],
        "parameters": {
            "stop_loss_pct": 0.004,
            "take_profit_pct": 0.005,
            "cooldown_minutes": 5,
        }
    },
    
    # MEAN_REVERSION Strategy
    "MEAN_REVERSION": {
        "stocks": ["ICICIBANK", "AXISBANK", "HDFCBANK", "BHARTIARTL", "JSWSTEEL"],
        "parameters": {
            "bb_period": 20,
            "bb_std_dev": 2.0,
            "rsi_period": 14,
            "stop_loss_pct": 0.0035,
            "take_profit_pct": 0.006,
            "cooldown_minutes": 8,
        }
    },
    
    # OPENING_RANGE_BREAKOUT Strategy
    "OPENING_RANGE_BREAKOUT": {
        "stocks": ["ASIANPAINT", "BRITANNIA", "EICHERMOT", "ULTRACEMCO", "ADANIPORTS"],
        "parameters": {
            "range_minutes": 15,
            "breakout_threshold": 0.003,
            "stop_loss_pct": 0.004,
            "take_profit_pct": 0.007,
            "cooldown_minutes": 12,
        }
    },
}

# Global backtesting configuration
BACKTEST_CONFIG = {
    "initial_capital": 100000.0,
    "risk_per_trade_pct": 0.01,
    "max_concurrent_trades": 3,
    "max_daily_loss": 1000.0,
    "start_date": "2023-01-01",
    "end_date": "2024-01-01",
    
    # YFinance settings
    "yfinance": {
        "max_retries": 5,
        "initial_retry_delay": 2,  # seconds
        "timeout": 30,
        "max_backoff_delay": 120,  # 2 minutes max
    },
    
    # Logging
    "verbose": True,
    "log_trades": True,
}

def get_strategy_config(strategy_name: str) -> dict:
    """Get configuration for a specific strategy."""
    if strategy_name not in STRATEGY_CONFIG:
        raise ValueError(f"Unknown strategy: {strategy_name}. Available: {list(STRATEGY_CONFIG.keys())}")
    return STRATEGY_CONFIG[strategy_name]

def get_symbols_for_strategy(strategy_name: str) -> list:
    """Get list of symbols to backtest for a strategy."""
    config = get_strategy_config(strategy_name)
    return config.get("stocks", [])

def get_all_strategies() -> list:
    """Get all available strategies."""
    return list(STRATEGY_CONFIG.keys())

def get_strategy_symbols_mapping() -> dict:
    """Get mapping of strategies to their symbols."""
    return {name: cfg.get("stocks", []) for name, cfg in STRATEGY_CONFIG.items()}
    """Get all available strategies."""
    return list(STRATEGY_CONFIG.keys())

def get_strategy_symbols_mapping() -> dict:
    """Get mapping of strategy to symbols."""
    return {
        strategy: config["symbols"]
        for strategy, config in STRATEGY_CONFIG.items()
    }
