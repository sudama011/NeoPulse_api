import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB

from app.db.session import get_session
from app.models.config import SystemConfig
from app.models.market_data import InstrumentMaster
from app.risk.manager import risk_manager
from app.schemas.requests import StartRequest
from app.strategy.engine import strategy_engine

logger = logging.getLogger("API_Engine")
router = APIRouter()


@router.post("/start")
async def start_bot(data: StartRequest, background_tasks: BackgroundTasks, session=Depends(get_session)):
    """
    🟢 START: Saves config to DB -> Reloads Components -> Starts Trading.
    """
    if strategy_engine._running:
        return {"status": "error", "message": "Bot is already running! Stop it first."}

    logger.info(f"📥 Start Request: {len(data.symbols)} symbols, Strategy={data.strategy}")

    # 1. RESOLVE TOKENS (Validate Symbols)
    symbol_map = []
    try:
        stmt = select(InstrumentMaster.trading_symbol, InstrumentMaster.token).where(
            InstrumentMaster.trading_symbol.in_(data.symbols)
        )
        result = await session.execute(stmt)

        found_symbols = []
        for row in result.fetchall():
            sym, tok = row
            found_symbols.append(sym)
            symbol_map.append(
                {"name": sym, "token": str(tok), "strategy": data.strategy, "params": data.strategy_params}
            )

        # Validation
        missing = set(data.symbols) - set(found_symbols)
        if missing:
            return {"status": "error", "message": f"Invalid Symbols (Not in Master): {missing}"}

    except Exception as e:
        return {"status": "error", "message": f"DB Error: {str(e)}"}

    # 2. PERSISTENCE (Save State to DB)
    # We update two keys: 'current_state' (Risk/Global) and 'strategy_config' (The list of active bots)

    # A. Global Config
    result = await session.execute(select(SystemConfig).where(SystemConfig.key == "current_state"))
    global_conf = result.scalars().first()
    if not global_conf:
        global_conf = SystemConfig(key="current_state")
        session.add(global_conf)

    global_conf.capital = data.capital
    global_conf.leverage = data.leverage
    global_conf.max_daily_loss = data.max_daily_loss
    global_conf.max_concurrent_trades = data.max_concurrent_trades

    # Pack Risk Params for JSONB
    risk_json = data.risk_params.copy() if data.risk_params else {}
    risk_json.update({"sizing_method": data.sizing_method, "risk_per_trade_pct": data.risk_per_trade_pct})
    global_conf.risk_params = risk_json

    # B. Strategy Config
    result_s = await session.execute(select(SystemConfig).where(SystemConfig.key == "strategy_config"))
    strat_conf = result_s.scalars().first()
    if not strat_conf:
        strat_conf = SystemConfig(key="strategy_config")
        session.add(strat_conf)

    strat_conf.symbols = {"SYMBOLS": symbol_map}  # JSONB structure used by StrategyEngine

    await session.commit()

    # 3. RELOAD & LAUNCH
    # Now that DB is updated, we tell components to refresh themselves

    await risk_manager.initialize()  # Reloads Risk Limits & PnL
    await strategy_engine.initialize()  # Reloads Strategy List from 'strategy_config'

    # Start the loops
    await strategy_engine.start()

    return {
        "status": "success",
        "message": f"Bot started with {len(symbol_map)} strategies.",
        "config": {"risk": data.sizing_method, "leverage": data.leverage},
    }


@router.post("/stop")
async def stop_bot():
    """
    🔴 STOP: Gracefully stops all strategies.
    """
    await strategy_engine.stop()
    return {"status": "success", "message": "Bot stopped. Positions remain open (manual square-off required)."}
