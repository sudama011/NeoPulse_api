from fastapi import APIRouter
from app.services.strategy.manager import strategy_engine
from app.core.settings import settings

router = APIRouter()

@router.get("/health")
async def health_check():
    """
    Simple heartbeat to check if the container is alive.
    """
    return {
        "status": "online",
        "mode": "PAPER" if settings.PAPER_TRADING else "LIVE",
        "strategies_active": len(strategy_engine.strategies)
    }

@router.get("/status")
async def get_strategy_status():
    """
    Returns the real-time PnL and Position of every active strategy.
    """
    report = {}
    for token, strategy in strategy_engine.strategies.items():
        # Calculate PnL on the fly for display
        pnl = 0.0
        current_price = strategy.current_candle.get('close', 0) if strategy.current_candle else 0
        
        if strategy.position != 0:
            if strategy.position > 0: # Long
                pnl = (current_price - strategy.entry_price) * strategy.position
            else: # Short
                pnl = (strategy.entry_price - current_price) * abs(strategy.position)

        report[strategy.symbol] = {
            "active": True,
            "position": strategy.position,
            "entry_price": strategy.entry_price,
            "current_price": current_price,
            "unrealized_pnl": round(pnl, 2),
            "last_update": strategy.current_candle.get('start_time') if strategy.current_candle else None
        }
    return report