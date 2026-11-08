"""Live Kotak Neo broker (SDK v3)."""

import asyncio
import contextlib
import logging
from typing import Any, Optional

from neo_api_client.exceptions import OpenApiException
from neo_api_client.websocket.orderfeed import OrderUpdate

from app.broker.base import Broker, BrokerUnavailable, OrderRejected
from app.broker.kotak_session import KotakSession, is_auth_error, response_error
from app.domain.models import BrokerOrder, BrokerPosition, OrderRequest, OrderStatus, Side

logger = logging.getLogger("KotakBroker")

_TERMINAL_STATUS = {
    "complete": OrderStatus.FILLED,
    "traded": OrderStatus.FILLED,
    "rejected": OrderStatus.REJECTED,
    "cancelled": OrderStatus.CANCELLED,
    "canceled": OrderStatus.CANCELLED,
}


def map_status(raw: Optional[str]) -> OrderStatus:
    return _TERMINAL_STATUS.get((raw or "").strip().lower(), OrderStatus.OPEN)


def _num(value: Any) -> float:
    try:
        return float(value) if value not in (None, "", "--") else 0.0
    except (TypeError, ValueError):
        return 0.0


def parse_order(row: dict) -> Optional[BrokerOrder]:
    """Maps an order_report() row / order-feed payload to a BrokerOrder."""
    order_id = row.get("nOrdNo")
    if not order_id:
        return None
    reason = row.get("rejRsn")
    return BrokerOrder(
        broker_order_id=str(order_id),
        status=map_status(row.get("ordSt") or row.get("stat")),
        filled_qty=int(_num(row.get("fldQty"))),
        avg_price=_num(row.get("avgPrc")),
        qty=int(_num(row.get("qty"))),
        client_id=(row.get("GuiOrdId") or None),
        token=str(row["tok"]) if row.get("tok") else None,
        message=None if reason in (None, "", "--", "NA") else str(reason),
    )


def parse_position(row: dict) -> BrokerPosition:
    """Net quantity / average price per the SDK's positions.md formulas."""
    buy_qty = _num(row.get("cfBuyQty")) + _num(row.get("flBuyQty"))
    sell_qty = _num(row.get("cfSellQty")) + _num(row.get("flSellQty"))
    buy_amt = _num(row.get("cfBuyAmt")) + _num(row.get("buyAmt"))
    sell_amt = _num(row.get("cfSellAmt")) + _num(row.get("sellAmt"))
    factor = (
        (_num(row.get("multiplier")) or 1.0)
        * ((_num(row.get("genNum")) or 1.0) / (_num(row.get("genDen")) or 1.0))
        * ((_num(row.get("prcNum")) or 1.0) / (_num(row.get("prcDen")) or 1.0))
    )

    net = int(buy_qty - sell_qty)
    if net > 0:
        avg = buy_amt / (buy_qty * factor)
    elif net < 0:
        avg = sell_amt / (sell_qty * factor)
    else:
        avg = 0.0
    return BrokerPosition(
        token=str(row.get("tok", "")),
        symbol=str(row.get("trdSym", "")),
        product=str(row.get("prod", "")),
        net_qty=net,
        avg_price=round(avg, 4),
    )


class KotakBroker(Broker):
    is_paper = False

    def __init__(self, session: KotakSession):
        super().__init__()
        self.session = session
        self._feed_task: Optional[asyncio.Task] = None

    @property
    def client(self):
        return self.session.client

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await self.session.ensure_login()
        self._feed_task = asyncio.create_task(self._order_feed_loop(), name="kotak-order-feed")

    async def stop(self) -> None:
        if self._feed_task:
            self._feed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._feed_task

    async def _order_feed_loop(self) -> None:
        """Push channel for order updates. Polling in the OMS covers any gap while this is down."""
        delay = 2
        while True:
            try:
                await self.session.ensure_login()
                async with self.client.create_order_feed() as feed:
                    logger.info("📬 Order feed connected")
                    delay = 2
                    async for msg in feed:
                        if not isinstance(msg, OrderUpdate):
                            continue
                        payload = msg.data.model_dump(by_alias=True)
                        payload.update(msg.data.model_extra or {})
                        order = parse_order(payload)
                        if order:
                            await self._emit(order)
                logger.warning("📭 Order feed ended; reconnecting")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("📭 Order feed error: %s (retry in %ss)", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    # ------------------------------------------------------------------ orders
    async def place_order(self, req: OrderRequest) -> str:
        kwargs = dict(
            exchange_segment=req.exchange_segment,
            product=req.product.value,
            price=f"{req.price:.2f}" if req.price else "0",
            order_type=req.order_type.value,
            quantity=str(req.qty),
            validity="DAY",
            trading_symbol=req.symbol,
            transaction_type="B" if req.side is Side.BUY else "S",
            trigger_price=f"{req.trigger_price:.2f}" if req.trigger_price else "0",
            tag=req.client_id,
        )
        try:
            resp = await self.session.call(self.client.place_order, retry_auth=False, **kwargs)
            if isinstance(resp, dict) and "Error Message" in resp:
                # SDK refused locally because there is no trade session: safe to re-login and resend once.
                await self.session.ensure_login(force=True)
                resp = await self.session.call(self.client.place_order, retry_auth=False, **kwargs)
        except Exception as e:  # thread pool / login failure: nothing was sent
            raise OrderRejected(f"could not send order: {e}") from e

        if isinstance(resp, dict) and resp.get("nOrdNo") and response_error(resp) is None:
            return str(resp["nOrdNo"])

        error = resp.get("Error") if isinstance(resp, dict) else None
        if isinstance(error, (OpenApiException, ValueError, TypeError)):
            # Client-side validation inside the SDK: request never left the process.
            raise OrderRejected(str(error))
        if isinstance(error, Exception):
            # Transport failure inside the SDK: the order may have reached the exchange.
            raise BrokerUnavailable(str(error))

        message = response_error(resp) or f"unexpected response: {resp}"
        if is_auth_error(message):
            raise OrderRejected(f"auth failure: {message}")
        raise OrderRejected(message)

    async def cancel_order(self, broker_order_id: str) -> None:
        resp = await self.session.call(self.client.cancel_order, order_id=broker_order_id)
        error = response_error(resp)
        if error:
            raise BrokerUnavailable(f"cancel {broker_order_id} failed: {error}")

    async def order_book(self) -> list[BrokerOrder]:
        resp = await self.session.call(self.client.order_report)
        error = response_error(resp)
        if error:
            # An empty book is reported by some backends as an error with no data.
            if "no data" in error.lower() or "not found" in error.lower():
                return []
            raise BrokerUnavailable(f"order_report failed: {error}")
        orders = (parse_order(r) for r in resp.get("data") or [] if isinstance(r, dict))
        return [o for o in orders if o is not None]

    async def positions(self) -> list[BrokerPosition]:
        resp = await self.session.call(self.client.positions)
        error = response_error(resp)
        if error:
            if "no data" in error.lower():
                return []
            raise BrokerUnavailable(f"positions failed: {error}")
        return [parse_position(r) for r in resp.get("data") or [] if isinstance(r, dict)]

    async def available_margin(self) -> Optional[float]:
        try:
            resp = await self.session.call(self.client.limits)
        except Exception as e:
            logger.warning("limits() failed: %s", e)
            return None
        if response_error(resp):
            return None
        value = resp.get("Net")
        return _num(value) if value is not None else None
