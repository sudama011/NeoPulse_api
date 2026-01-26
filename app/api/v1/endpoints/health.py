from datetime import datetime

import pytz
from fastapi import APIRouter

from app.core.bus import event_bus
from app.core.settings import settings
from app.services.risk.monitor import risk_monitor
from app.services.strategy.manager import strategy_engine

router = APIRouter()
IND = pytz.timezone("Asia/Kolkata")


@router.get("/health")
async def health_check():
    """
    Comprehensive health check for monitoring and alerting.

    Returns:
        {
            "status": "healthy" | "degraded" | "critical",
            "bot_running": bool,
            "timestamp": ISO timestamp,
            "components": {...}
        }
    """
    risk_status = await risk_monitor.get_status()
    event_bus_stats = event_bus.get_stats()

    # Determine overall health
    status = "healthy"

    # Critical: tick queue usage > 80%
    tick_usage = event_bus_stats["tick_queue_usage"]
    if tick_usage > 80:
        status = "degraded"
    if tick_usage > 95:
        status = "critical"

    # Critical: order queue near full
    if event_bus_stats["order_queue_size"] > 90:
        status = "critical"

    # Critical: losses exceed limit
    if risk_status["current_pnl"] <= -risk_status["max_daily_loss"]:
        status = "critical"

    return {
        "status": status,
        "timestamp": datetime.now(tz=IND).isoformat(),
        "bot": {
            "running": strategy_engine.is_running,
            "mode": "PAPER" if settings.PAPER_TRADING else "LIVE",
            "strategies_active": len(strategy_engine.strategies),
            "capital_available": strategy_engine.available_capital,
        },
        "risk": {
            "current_pnl": risk_status["current_pnl"],
            "max_daily_loss": risk_status["max_daily_loss"],
            "loss_utilization": f"{risk_status['loss_percentage']:.1f}%",
            "open_positions_count": risk_status["open_positions_count"],
            "max_trades": risk_status["max_concurrent_trades"],
        },
        "queues": {
            "tick_queue_usage": tick_usage,
            "tick_queue_size": event_bus_stats["tick_queue_size"],
            "ticks_dropped": event_bus_stats["ticks_dropped"],
            "order_queue_usage": f"{event_bus_stats['order_queue_size'] / event_bus_stats['order_queue_max'] * 100:.1f}%",
        },
    }


@router.get("/status")
async def get_strategy_status():
    """
    Returns real-time position, PnL, and metrics for all active strategies.

    Returns:
        {
            "timestamp": ISO timestamp,
            "strategies": {
                "SYMBOL": {
                    "position": int,
                    "entry_price": float,
                    "current_price": float,
                    "unrealized_pnl": float,
                    "pnl_pct": float,
                    ...
                }
            }
        }
    """
    report = {}

    for token, strategy in strategy_engine.strategies.items():
        # Get current price from latest candle
        current_price = 0.0
        if strategy.current_candle:
            current_price = strategy.current_candle.get("close", 0)
        elif strategy.candles:
            current_price = strategy.candles[-1].get("close", 0)

        # Calculate unrealized PnL
        pnl = 0.0
        pnl_pct = 0.0

        if strategy.position != 0 and strategy.entry_price > 0:
            if strategy.position > 0:  # Long
                pnl = (current_price - strategy.entry_price) * strategy.position
                pnl_pct = (current_price - strategy.entry_price) / strategy.entry_price
            else:  # Short
                pnl = (strategy.entry_price - current_price) * abs(strategy.position)
                pnl_pct = (strategy.entry_price - current_price) / strategy.entry_price

        report[strategy.symbol] = {
            "active": True,
            "position": strategy.position,
            "position_side": "LONG" if strategy.position > 0 else "SHORT" if strategy.position < 0 else "FLAT",
            "entry_price": round(strategy.entry_price, 2),
            "current_price": round(current_price, 2),
            "unrealized_pnl": round(pnl, 2),
            "pnl_percentage": f"{pnl_pct * 100:.2f}%" if pnl_pct != 0 else "0.00%",
            "candles_count": len(strategy.candles),
            "last_signal": getattr(strategy, "last_signal", None),
            "last_update": (strategy.current_candle.get("start_time").isoformat() if strategy.current_candle else None),
        }

    return {
        "timestamp": datetime.now(tz=IND).isoformat(),
        "strategies": report,
        "summary": {
            "total_positions": sum(1 for s in report.values() if s["position"] != 0),
            "total_unrealized_pnl": sum(s["unrealized_pnl"] for s in report.values()),
        },
    }


@router.get("/metrics")
async def get_metrics():
    """
    Returns system metrics for monitoring dashboard.
    """
    risk_status = await risk_monitor.get_status()
    event_bus_stats = event_bus.get_stats()

    # Calculate aggregate metrics
    total_strategies = len(strategy_engine.strategies)
    active_strategies = sum(1 for s in strategy_engine.strategies.values() if s.position != 0)

    return {
        "timestamp": datetime.now(tz=IND).isoformat(),
        "trading": {
            "bot_running": strategy_engine.is_running,
            "total_strategies": total_strategies,
            "active_positions": active_strategies,
            "capital_allocated": strategy_engine.available_capital,
        },
        "risk": {
            "current_pnl": risk_status["current_pnl"],
            "max_daily_loss": risk_status["max_daily_loss"],
            "loss_utilization_pct": risk_status["loss_percentage"],
            "trades_executed": risk_status["open_positions_count"],
            "max_concurrent_trades": risk_status["max_concurrent_trades"],
        },
        "performance": {
            "ticks_processed": event_bus_stats["orders_queued"],
            "ticks_dropped": event_bus_stats["ticks_dropped"],
            "tick_queue_depth": event_bus_stats["tick_queue_size"],
            "order_queue_depth": event_bus_stats["order_queue_size"],
        },
    }
