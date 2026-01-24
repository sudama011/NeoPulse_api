from fastapi import APIRouter, BackgroundTasks, HTTPException
from app.modules.strategy.engine import strategy_engine
from app.adapters.telegram.client import telegram_client

router = APIRouter()

@router.post("/stop")
async def emergency_stop():
    """
    🚨 EMERGENCY KILL SWITCH.
    Stops the strategy engine immediately.
    """
    if not strategy_engine.is_running:
        return {"message": "Bot is already stopped."}

    strategy_engine.is_running = False
    await telegram_client.send_alert("🚨 <b>STOP COMMAND RECEIVED</b> via API")
    return {"message": "Bot stopping gracefully..."}

@router.post("/resume")
async def resume_bot(background_tasks: BackgroundTasks):
    """
    Resumes the strategy engine loop.
    """
    if strategy_engine.is_running:
        return {"message": "Bot is already running."}

    # Use BackgroundTasks to run the infinite loop so we don't block the API response
    background_tasks.add_task(strategy_engine.start)
    await telegram_client.send_alert("✅ <b>RESUME COMMAND RECEIVED</b> via API")
    return {"message": "Bot resuming operation..."}