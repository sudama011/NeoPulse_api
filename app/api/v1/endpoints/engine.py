from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from app.services.strategy.manager import strategy_engine
from app.adapters.telegram.client import telegram_client

router = APIRouter()


class StartRequest(BaseModel):
    capital: float = 50000.0  # Default value


@router.post("/start")
async def start_bot(data: StartRequest, background_tasks: BackgroundTasks):
    """
    🟢 MANUAL START.
    1. Sets the capital.
    2. Starts the engine.
    """
    if strategy_engine.is_running:
        return {"status": "error", "message": "Bot is already running!"}

    # 1. Set Capital
    strategy_engine.available_capital = data.capital

    # 2. Start Engine (Background Task)
    background_tasks.add_task(strategy_engine.start)

    # 3. Alert
    msg = (
        f"🟢 <b>BOT STARTED MANUALLY</b>\n"
        f"💰 Allocated Capital: ₹{data.capital:,.2f}\n"
        f"⏰ Auto-Stop: 03:15 PM"
    )
    await telegram_client.send_alert(msg)

    return {"status": "success", "message": "Bot started.", "capital": data.capital}


@router.post("/stop")
async def stop_bot():
    """
    🔴 MANUAL STOP (Panic).
    Does NOT auto-square off (unless you hit the panic endpoint).
    Just stops the loop.
    """
    strategy_engine.is_running = False
    await telegram_client.send_alert("🔴 <b>BOT STOPPED MANUALLY</b>")
    return {"status": "success", "message": "Bot stopped."}

@router.get("/status")
async def get_bot_status():
    """
    Returns the heartbeat for the Dashboard.
    """
    positions = []
    
    # Extract positions from Strategy Engine
    for token, strat in strategy_engine.strategies.items():
        if strat.position != 0:
            positions.append({
                "symbol": strat.symbol,
                "qty": strat.position,
                "ltp": getattr(strat, 'last_price', 0.0),
                "pnl": getattr(strat, 'current_pnl', 0.0) # Ensure strategy tracks this
            })

    return {
        "status": "running" if strategy_engine.is_running else "stopped",
        "capital_allocated": strategy_engine.available_capital,
        "open_positions": positions,
        "active_strategies": list(strategy_engine.strategies.keys())
    }