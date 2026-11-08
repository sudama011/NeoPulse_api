from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.api.schemas import (
    BulkCreateRequest,
    CreateStrategyRequest,
    KillSwitchRequest,
    OrderOut,
    StopStrategyRequest,
    StrategyOut,
)
from app.container import Container
from app.core.clock import now_ist, start_of_day, today_ist
from app.models import Order, StrategyInstance
from app.strategy import list_strategies
from app.strategy.engine import EngineError, NewInstance

router = APIRouter()


def container(request: Request) -> Container:
    return request.app.state.container


# ---------------------------------------------------------------------- system
@router.get("/health", tags=["System"])
async def health(c: Container = Depends(container)):
    pnl = c.risk.day_pnl()
    feed_age = c.feed.seconds_since_data
    return {
        "time": now_ist().isoformat(),
        "mode": c.mode,
        "started": c.started,
        "broker_session": c.kotak.is_logged_in,
        "market_feed": {
            "connected": c.feed.connected,
            "seconds_since_data": None if feed_age == float("inf") else round(feed_age, 1),
        },
        "kill_switch": {"active": c.risk.kill_switch, "reason": c.risk.kill_reason},
        "day_pnl": {
            "realized": round(pnl.realized, 2),
            "charges": round(pnl.charges, 2),
            "unrealized": round(pnl.unrealized, 2),
            "net": round(pnl.net, 2),
        },
        "strategies_running": len(c.engine.runners),
        "strategies_failing": c.engine.failures(),
        "open_orders": len(c.oms.open_orders()),
        "jobs": c.scheduler.jobs(),
    }


# ---------------------------------------------------------------------- strategies
@router.get("/strategies/types", tags=["Strategies"])
async def strategy_types():
    return list_strategies()


@router.get("/strategies", response_model=list[StrategyOut], tags=["Strategies"])
async def list_instances(include_stopped: bool = False, c: Container = Depends(container)):
    async with c.sf() as db:
        stmt = select(StrategyInstance).order_by(StrategyInstance.created_at)
        if not include_stopped:
            stmt = stmt.where(StrategyInstance.status == "ACTIVE")
        rows = (await db.execute(stmt)).scalars().all()
    return [_instance_out(r, c) for r in rows]


@router.post("/strategies", response_model=StrategyOut, status_code=201, tags=["Strategies"])
async def create_instance(body: CreateStrategyRequest, c: Container = Depends(container)):
    try:
        row = await c.engine.create(NewInstance(**body.model_dump()))
    except (EngineError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _instance_out(row, c)


@router.post("/strategies/bulk", tags=["Strategies"])
async def create_bulk(body: BulkCreateRequest, c: Container = Depends(container)):
    created, errors = [], {}
    for symbol in body.symbols:
        try:
            row = await c.engine.create(
                NewInstance(
                    strategy_type=body.strategy_type,
                    symbol=symbol,
                    capital=body.capital_per_symbol,
                    params=body.params,
                    timeframe=body.timeframe,
                    product=body.product,
                    risk_per_trade_pct=body.risk_per_trade_pct,
                    leverage=body.leverage,
                )
            )
            created.append({"id": row.id, "symbol": row.symbol})
        except (EngineError, ValueError) as e:
            errors[symbol] = str(e)
    return {"created": created, "errors": errors}


@router.post("/strategies/{instance_id}/stop", tags=["Strategies"])
async def stop_instance(instance_id: str, body: StopStrategyRequest | None = None, c: Container = Depends(container)):
    await _require_instance(c, instance_id)
    flatten = body.flatten if body else True
    flat = await c.engine.stop(instance_id, flatten=flatten, reason="stopped via API")
    return {"stopped": instance_id, "flat": flat}


@router.post("/strategies/{instance_id}/resume", tags=["Strategies"])
async def resume_instance(instance_id: str, c: Container = Depends(container)):
    try:
        await c.engine.resume(instance_id)
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"resumed": instance_id, "running": instance_id in c.engine.runners}


# ---------------------------------------------------------------------- orders & risk
@router.get("/orders", response_model=list[OrderOut], tags=["Trading"])
async def orders(days: int = Query(1, ge=1, le=30), instance_id: str | None = None, c: Container = Depends(container)):
    since: datetime = start_of_day(today_ist() - timedelta(days=days - 1))
    async with c.sf() as db:
        stmt = select(Order).where(Order.created_at >= since).order_by(Order.created_at.desc())
        if instance_id:
            stmt = stmt.where(Order.instance_id == instance_id)
        return (await db.execute(stmt)).scalars().all()


@router.get("/positions", tags=["Trading"])
async def positions(c: Container = Depends(container)):
    out = []
    for iid, pos in c.positions.open_positions().items():
        token = c.positions.token_of(iid)
        ltp = c.bus.last_price(token) if token else None
        out.append(
            {
                "instance_id": iid,
                "token": token,
                "qty": pos.qty,
                "avg_price": pos.avg_price,
                "ltp": ltp,
                "unrealized": round(pos.unrealized(ltp), 2) if ltp else None,
            }
        )
    return out


@router.post("/risk/kill-switch", tags=["Risk"])
async def kill_switch(body: KillSwitchRequest, c: Container = Depends(container)):
    if body.active:
        await c.risk.trip(body.reason)
    else:
        await c.risk.reset()
    return {"active": c.risk.kill_switch, "reason": c.risk.kill_reason}


@router.post("/risk/flatten-all", tags=["Risk"])
async def flatten_all(c: Container = Depends(container)):
    await c.engine.flatten_all("manual flatten via API")
    return {"open_positions": len(c.positions.open_positions())}


# ---------------------------------------------------------------------- helpers
async def _require_instance(c: Container, instance_id: str) -> StrategyInstance:
    async with c.sf() as db:
        row = await db.get(StrategyInstance, instance_id)
    if row is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return row


def _instance_out(row: StrategyInstance, c: Container) -> StrategyOut:
    out = StrategyOut.model_validate(row)
    pos = c.positions.get(row.id)
    out.position_qty, out.avg_price = pos.qty, pos.avg_price
    runner = c.engine.runners.get(row.id)
    out.running = bool(runner and runner.running)
    if runner:
        out.state = runner.state
    out.start_error = c.engine.failures().get(row.id)
    out.day_pnl = round(c.positions.day_pnl(c.bus.last_price, row.id).net, 2)
    return out
