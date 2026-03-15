import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.data.master import master_data
from app.db.session import get_session
from app.models.strategy import StrategyInstance, TradingSession
from app.risk.manager import risk_manager
from app.schemas.requests import StartRequest, StopRequest
from app.strategy.engine import strategy_engine

logger = logging.getLogger("API_Engine")
router = APIRouter()


@router.post("/start")
async def start_bot(data: StartRequest, session=Depends(get_session)):
    """
    🟢 START: Add strategies incrementally.
    - If no active session exists, creates one with provided capital/limits
    - If session exists, adds strategies to it
    - Reloads and starts the engine
    """
    logger.info(f"📥 Start Request: {len(data.strategies)} strategies")

    try:
        # 1. GET OR CREATE TRADING SESSION
        result = await session.execute(select(TradingSession).where(TradingSession.is_active == True))
        trading_session = result.scalars().first()

        if not trading_session:
            # Create new session
            if not data.capital or not data.max_daily_loss or not data.max_concurrent_trades:
                return {
                    "status": "error",
                    "message": "No active session found. Please provide capital, max_daily_loss, and max_concurrent_trades.",
                }

            trading_session = TradingSession(
                is_active=True,
                capital=data.capital,
                max_daily_loss=data.max_daily_loss,
                max_concurrent_trades=data.max_concurrent_trades,
            )
            session.add(trading_session)
            await session.flush()
            logger.info(f"✅ Created new TradingSession {trading_session.id}")
        else:
            logger.info(f"📌 Using existing TradingSession {trading_session.id}")

        # 2. CREATE STRATEGY INSTANCES
        created_strategies = []
        for strategy_config in data.strategies:
            # Get token from master data
            token = master_data.get_token(strategy_config.symbol)
            if not token:
                logger.warning(f"⚠️ Skipping {strategy_config.symbol}: not found in master data")
                continue

            # Check if strategy already exists for this symbol
            existing = await session.execute(
                select(StrategyInstance).where(
                    StrategyInstance.symbol == strategy_config.symbol,
                    StrategyInstance.strategy_type == strategy_config.strategy_type,
                    StrategyInstance.is_active == True,
                )
            )
            if existing.scalars().first():
                logger.warning(f"⚠️ Skipping {strategy_config.symbol}: {strategy_config.strategy_type} already active")
                continue

            # Create strategy instance
            instance_name = f"{strategy_config.strategy_type}_{strategy_config.symbol}"
            strategy_instance = StrategyInstance(
                session_id=trading_session.id,
                instance_name=instance_name,
                strategy_type=strategy_config.strategy_type,
                symbol=strategy_config.symbol,
                token=token,
                leverage=strategy_config.leverage,
                sizing_method=strategy_config.sizing_method,
                risk_per_trade_pct=strategy_config.risk_per_trade_pct,
                is_active=True,
            )
            session.add(strategy_instance)
            created_strategies.append(instance_name)
            logger.info(
                f"  ✅ {instance_name}: leverage={strategy_config.leverage}x, "
                f"sizing={strategy_config.sizing_method}, risk={strategy_config.risk_per_trade_pct*100}%"
            )

        await session.commit()

        if not created_strategies:
            return {"status": "error", "message": "No strategies were created (all skipped or invalid)"}

        # 3. RELOAD & START ENGINE
        await risk_manager.initialize()
        await strategy_engine.initialize()

        if not strategy_engine._running:
            await strategy_engine.start()

        return {
            "status": "success",
            "message": f"Started {len(created_strategies)} strategies",
            "strategies": created_strategies,
            "session_id": str(trading_session.id),
        }

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Error starting strategies: {e}")
        return {"status": "error", "message": f"Database Error: {str(e)}"}


@router.post("/stop")
async def stop_bot(data: StopRequest, session=Depends(get_session)):
    """
    🔴 STOP: Granular strategy stopping.
    - stop_all=True: Stops all strategies and deactivates session
    - symbol=X: Stops all strategies for symbol X
    - strategy_instance_id=Y: Stops specific strategy instance
    """
    try:
        stopped_strategies = []

        if data.stop_all:
            # Stop all active strategies
            result = await session.execute(select(StrategyInstance).where(StrategyInstance.is_active == True))
            instances = result.scalars().all()

            for inst in instances:
                inst.is_active = False
                stopped_strategies.append(inst.instance_name)

            # Deactivate session
            session_result = await session.execute(select(TradingSession).where(TradingSession.is_active == True))
            trading_session = session_result.scalars().first()
            if trading_session:
                trading_session.is_active = False

            await session.commit()
            await strategy_engine.stop()

            return {
                "status": "success",
                "message": f"Stopped all {len(stopped_strategies)} strategies and deactivated session",
                "stopped": stopped_strategies,
            }

        elif data.symbol:
            # Stop all strategies for this symbol
            result = await session.execute(
                select(StrategyInstance).where(
                    StrategyInstance.symbol == data.symbol, StrategyInstance.is_active == True
                )
            )
            instances = result.scalars().all()

            for inst in instances:
                inst.is_active = False
                stopped_strategies.append(inst.instance_name)

            await session.commit()
            await strategy_engine.initialize()  # Reload without stopped strategies

            return {
                "status": "success",
                "message": f"Stopped {len(stopped_strategies)} strategies for {data.symbol}",
                "stopped": stopped_strategies,
            }

        elif data.strategy_instance_id:
            # Stop specific strategy instance
            result = await session.execute(
                select(StrategyInstance).where(
                    StrategyInstance.id == data.strategy_instance_id, StrategyInstance.is_active == True
                )
            )
            instance = result.scalars().first()

            if not instance:
                return {"status": "error", "message": "Strategy instance not found or already stopped"}

            instance.is_active = False
            await session.commit()
            await strategy_engine.initialize()  # Reload without stopped strategy

            return {
                "status": "success",
                "message": f"Stopped strategy {instance.instance_name}",
                "stopped": [instance.instance_name],
            }

        else:
            return {"status": "error", "message": "Please specify stop_all=true, symbol, or strategy_instance_id"}

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Error stopping strategies: {e}")
        return {"status": "error", "message": f"Error: {str(e)}"}
