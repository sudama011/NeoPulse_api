import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.future import select

from app.db.session import get_session
from app.models.config import SystemConfig
from app.models.market_data import InstrumentMaster
from app.risk.manager import PositionConfig, risk_manager
from app.schemas.requests import StartRequest
from app.strategy.engine import strategy_engine

logger = logging.getLogger("API_Engine")
router = APIRouter()


@router.post("/start")
async def start_bot(data: StartRequest, background_tasks: BackgroundTasks, session=Depends(get_session)):
    """
    🟢 DYNAMIC START.
    """
    if strategy_engine.is_running:
        return {"status": "error", "message": "Bot is already running! Stop it first."}

    logger.info(f"📥 Start Request: {len(data.symbols)} symbols, Strategy={data.strategy}, Sizing={data.sizing_method}")

    # 1. PERSISTENCE (Save Config to DB)
    result = await session.execute(select(SystemConfig).where(SystemConfig.key == "current_state"))
    config_entry = result.scalars().first()

    if not config_entry:
        config_entry = SystemConfig(key="current_state")
        session.add(config_entry)

    config_entry.capital = data.capital
    config_entry.leverage = data.leverage
    config_entry.strategy_name = data.strategy
    config_entry.symbols = data.symbols
    config_entry.max_daily_loss = data.max_daily_loss
    config_entry.max_concurrent_trades = data.max_concurrent_trades

    # Store the new sizing config in the JSON blob for persistence
    data.risk_params["sizing_method"] = data.sizing_method
    data.risk_params["risk_per_trade_pct"] = data.risk_per_trade_pct
    config_entry.risk_params = data.risk_params

    await session.commit()

    # 2. CONFIGURE SERVICES
    strategy_engine.available_capital = data.capital

    # A. Update Position Sizer Config (The crucial part for Martingale)
    new_pos_config = PositionConfig(
        method=data.sizing_method, risk_per_trade_pct=data.risk_per_trade_pct, leverage=data.leverage
    )
    # Explicitly update the manager's sizer config
    risk_manager.sizer.config = new_pos_config

    # B. Update Risk Sentinel Config
    await risk_manager.sentinel.update_config(  # Assuming you added this helper, or access config directly
        max_daily_loss=data.max_daily_loss, max_concurrent_trades=data.max_concurrent_trades
    )
    # Manually update config object if helper doesn't exist yet
    risk_manager.sentinel.config.max_daily_loss = data.max_daily_loss
    risk_manager.sentinel.config.max_open_trades = data.max_concurrent_trades

    # C. Strategy Configuration (Resolve Symbols)
    symbol_to_token = {}
    try:
        stmt = select(InstrumentMaster.trading_symbol, InstrumentMaster.token).where(
            InstrumentMaster.trading_symbol.in_(data.symbols)
        )
        result = await session.execute(stmt)
        for row in result.fetchall():
            symbol_to_token[row[0]] = str(row[1])

        # [Remaining symbol validation logic...]

    except Exception as e:
        return {"status": "error", "message": f"DB Error: {str(e)}"}

    # 3. START ENGINE
    # Pass the full config so strategy engine knows about tokens
    run_config = {
        "symbols": data.symbols,
        "strategy": data.strategy,
        "tokens": symbol_to_token,  # Pass the resolved map
        "params": data.strategy_params,
    }

    # Note: Ensure strategy_engine.start accepts this dictionary structure
    background_tasks.add_task(strategy_engine.start, run_config)

    return {"status": "success", "message": f"Bot started with {data.sizing_method} sizing."}


# [Stop and Status endpoints remain largely similar, just verify risk_monitor references are gone]
