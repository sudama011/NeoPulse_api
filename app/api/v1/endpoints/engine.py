from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.future import select
from app.db.session import get_session
from app.models.config import SystemConfig
from app.services.strategy.manager import strategy_engine
from app.services.risk.monitor import risk_monitor
from app.adapters.telegram_client import telegram_client
from app.adapters.neo_client import neo_client
import logging

logger = logging.getLogger("API_Engine")
router = APIRouter()

# --- Request Model ---
class StartRequest(BaseModel):
    capital: float = Field(..., gt=0, description="Allocated Trading Capital")
    symbols: list[str] = Field(..., min_length=1, description="List of Trading Symbols")
    strategy: str = "MOMENTUM_TREND"
    strategy_params: dict = {}
    leverage: float = 1.0
    max_daily_loss: float = 1000.0
    max_concurrent_trades: int = 3
    risk_params: dict = {}

@router.post("/start")
async def start_bot(
    data: StartRequest, 
    background_tasks: BackgroundTasks, 
    session = Depends(get_session)
):
    """
    🟢 DYNAMIC START.
    1. Saves config to DB (Persistence).
    2. Configures Risk & Strategy Engines.
    3. Starts the loop.
    """
    if strategy_engine.is_running:
        return {"status": "error", "message": "Bot is already running! Stop it first to re-configure."}

    logger.info(f"📥 Received Start Request: {len(data.symbols)} symbols, Strategy={data.strategy}")

    # 1. PERSISTENCE: Save Config to DB
    # Check if config exists, update or create
    result = await session.execute(select(SystemConfig).where(SystemConfig.key == "current_state"))
    config_entry = result.scalars().first()
    
    if not config_entry:
        config_entry = SystemConfig(key="current_state")
        session.add(config_entry)
    
    # Update fields
    config_entry.capital = data.capital
    config_entry.leverage = data.leverage
    config_entry.strategy_name = data.strategy
    config_entry.symbols = data.symbols
    config_entry.strategy_params = data.strategy_params
    config_entry.max_daily_loss = data.max_daily_loss
    config_entry.max_concurrent_trades = data.max_concurrent_trades
    config_entry.risk_params = data.risk_params
    
    await session.commit()

    # 2. CONFIGURE SERVICES
    # A. Capital
    strategy_engine.available_capital = data.capital
    
    # B. Risk
    risk_monitor.update_config(
        max_daily_loss=data.max_daily_loss,
        max_concurrent_trades=data.max_concurrent_trades,
        risk_params=data.risk_params
    )

    # C. Strategy (Need to fetch tokens for symbols)
    # NOTE: In a real app, query InstrumentMaster here to get tokens for symbols
    # For now, we iterate and assume you implement the token lookup
    valid_symbols = []
    
    # Example Token Lookup (You need to implement this helper)
    # tokens = await get_tokens_for_symbols(data.symbols, session) 
    
    # For now, we skip the lookup code to keep this readable.
    # You MUST map Symbol -> Token here before calling manager.
    
    # strategy_engine.configure(data.strategy, valid_symbols, data.strategy_params)

    # 3. START ENGINE
    background_tasks.add_task(strategy_engine.start)

    # 4. ALERT
    msg = (
        f"🟢 <b>BOT STARTED</b>\n"
        f"💰 Capital: ₹{data.capital:,.2f}\n"
        f"📊 Strategy: {data.strategy}\n"
        f"🛡️ Max Loss: ₹{data.max_daily_loss}"
    )
    await telegram_client.send_alert(msg)

    return {
        "status": "success", 
        "message": "Bot configured and started.", 
        "config_id": config_entry.id
    }

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