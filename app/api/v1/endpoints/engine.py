from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.future import select
from app.db.session import get_session
from app.models.config import SystemConfig
from app.models.market_data import InstrumentMaster
from app.services.strategy.manager import strategy_engine
from app.services.risk.monitor import risk_monitor
from app.adapters.telegram_client import telegram_client
from app.adapters.neo_client import neo_client
import logging
from app.schemas.requests import StartRequest

logger = logging.getLogger("API_Engine")
router = APIRouter()

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
    await risk_monitor.update_config(
        max_daily_loss=data.max_daily_loss,
        max_concurrent_trades=data.max_concurrent_trades,
        risk_params=data.risk_params
    )

    # C. Strategy - Fetch tokens for symbols from InstrumentMaster
    symbol_to_token = {}
    try:
        stmt = select(
            InstrumentMaster.trading_symbol, 
            InstrumentMaster.instrument_token
        ).where(InstrumentMaster.trading_symbol.in_(data.symbols))
        result = await session.execute(stmt)
        
        for row in result.fetchall():
            symbol_to_token[row[0]] = str(row[1])
        
        # Validate that all requested symbols were found
        missing_symbols = [s for s in data.symbols if s not in symbol_to_token]
        if missing_symbols:
            logger.warning(f"⚠️ Symbols not found in InstrumentMaster: {missing_symbols}")
            return {
                "status": "error",
                "message": f"Symbols not found: {missing_symbols}. Please ensure they exist in the instrument master database."
            }
        
        logger.info(f"✅ Resolved {len(symbol_to_token)} symbols to tokens")
        
    except Exception as e:
        logger.error(f"❌ Failed to fetch symbols from InstrumentMaster: {e}")
        return {
            "status": "error",
            "message": f"Database error while fetching instrument tokens: {str(e)}"
        }
    
    # Configure strategy engine with validated symbols and parameters
    try:
        await strategy_engine.configure(
            strategy_name=data.strategy,
            symbols=data.symbols,
            params=data.strategy_params
        )
        logger.info(f"✅ Strategy engine configured with {len(data.symbols)} symbols")
    except ValueError as e:
        logger.error(f"❌ Strategy configuration error: {e}")
        return {
            "status": "error",
            "message": f"Strategy configuration failed: {str(e)}"
        }
    except Exception as e:
        logger.error(f"❌ Unexpected error during strategy configuration: {e}")
        return {
            "status": "error",
            "message": f"Failed to configure strategy: {str(e)}"
        }

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