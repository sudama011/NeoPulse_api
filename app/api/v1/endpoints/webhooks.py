from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.core.settings import settings
from app.notifications.manager import notification_manager
from app.strategy.engine import strategy_engine

router = APIRouter()


# 1. Define the Expected Signal Format (Data Model)
class TradingViewSignal(BaseModel):
    passphrase: str
    symbol: str  # e.g., "RELIANCE"
    action: str  # "BUY" or "SELL"
    quantity: int = 1
    price: float = 0.0


@router.post("/tradingview")
async def receive_signal(signal: TradingViewSignal):
    """
    Receives alerts from TradingView and routes them to the correct strategy.
    """
    # A. Security Check
    # (Add WEBHOOK_PASSPHRASE to your .env to prevent hackers from spamming you)
    valid_passphrase = getattr(settings, "WEBHOOK_PASSPHRASE", "1234")
    if signal.passphrase != valid_passphrase:
        raise HTTPException(status_code=401, detail="Invalid Passphrase")

    # B. Find the Strategy
    # We look up the strategy by Symbol (e.g., "RELIANCE")
    target_strategy = None
    for token, strat in strategy_engine.strategies.items():
        if strat.symbol == signal.symbol:
            target_strategy = strat
            break

    if not target_strategy:
        await notification_manager.push(f"⚠️ <b>IGNORED SIGNAL</b>\nNo active strategy found for {signal.symbol}")
        return {"status": "ignored", "reason": "Strategy not found"}

    # C. Execute Logic
    # We inject the external signal directly into the strategy
    await notification_manager.push(f"📨 <b>WEBHOOK RECEIVED</b>\n{signal.action} {signal.symbol}")

    # Trigger the trade manually
    await target_strategy.execute_trade(signal.action.upper(), signal.price)

    return {"status": "processed", "signal": signal.action}
