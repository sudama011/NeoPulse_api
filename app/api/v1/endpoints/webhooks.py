from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.core.settings import settings
from app.notifications.manager import notification_manager
from app.strategy.engine import strategy_engine

router = APIRouter()


class TradingViewSignal(BaseModel):
    passphrase: str
    symbol: str
    action: str  # BUY/SELL
    price: float = 0.0
    quantity: Optional[int] = None  # Optional override


@router.post("/tradingview")
async def receive_signal(signal: TradingViewSignal, bg_tasks: BackgroundTasks):
    """
    Route TV Alert -> Specific Strategy Instance
    """
    # 1. Auth
    if signal.passphrase != settings.WEBHOOK_PASSPHRASE:
        raise HTTPException(status_code=401, detail="Invalid Passphrase")

    # 2. Locate Strategy
    # We search active strategies for one matching the symbol
    target_strat = None
    for token, strat in strategy_engine.active_strategies.items():
        if strat.symbol == signal.symbol:
            target_strat = strat
            break

    if not target_strat:
        msg = f"⚠️ Ignored Webhook: No active strategy for {signal.symbol}"
        bg_tasks.add_task(notification_manager.push, msg)
        return {"status": "ignored", "reason": "Strategy not found"}

    # 3. Execute
    action = signal.action.upper()

    if action == "BUY":
        # Strategy calculates size automatically if qty not provided
        # We pass confidence=2.0 to indicate this is a strong external signal
        await target_strat.buy(price=signal.price, sl=signal.price * 0.99, confidence=2.0, tag="WEBHOOK")

    elif action == "SELL":
        await target_strat.sell(price=signal.price, qty=signal.quantity, tag="WEBHOOK")

    msg = f"📨 Webhook Executed: {action} {signal.symbol}"
    bg_tasks.add_task(notification_manager.push, msg)

    return {"status": "processed", "strategy": target_strat.name}
