from datetime import datetime

import pytz
from fastapi import APIRouter

from app.core.settings import settings
from app.data.stream import data_stream
from app.risk.manager import risk_manager
from app.strategy.engine import strategy_engine

router = APIRouter()
IND = pytz.timezone("Asia/Kolkata")


@router.get("/health")
async def health_check():
    """
    System Health & Vital Stats.
    """
    # 1. Risk Metrics
    sentinel = risk_manager.sentinel

    # 2. Queue Metrics
    q_size = data_stream.tick_queue.qsize()
    q_usage = (q_size / data_stream.tick_queue.maxsize) * 100

    # Determine Health
    status = "healthy"
    if q_usage > 80:
        status = "degraded"
    if sentinel.config.kill_switch_active:
        status = "critical"

    return {
        "status": status,
        "timestamp": datetime.now(tz=IND).isoformat(),
        "mode": "PAPER" if settings.PAPER_TRADING else "LIVE",
        "engine_running": strategy_engine._running,
        "risk": {
            "pnl": sentinel.current_pnl,
            "max_loss": sentinel.config.max_daily_loss,
            "kill_switch": sentinel.config.kill_switch_active,
            "open_trades": sentinel.open_trades,
            "max_trades": sentinel.config.max_concurrent_trades,
        },
        "performance": {"tick_queue_size": q_size, "tick_queue_usage_pct": round(q_usage, 2)},
    }


@router.get("/status")
async def get_strategy_status():
    """
    Real-time status of all active strategies.
    """
    report = {}

    for token, strat in strategy_engine.active_strategies.items():
        # Calculate approximate Unrealized PnL if we have price info
        # Note: Strategy 'avg_price' is populated via 'sync_position'

        report[strat.symbol] = {
            "strategy": strat.name,
            "active": strat.is_active,
            "position": strat.position,
            "avg_price": strat.avg_price,
            "last_trade_time": strat.last_trade_time,
        }

    return {"timestamp": datetime.now(tz=IND).isoformat(), "total_active": len(report), "strategies": report}
