import logging
from typing import Any, Optional

import pyotp
from neo_api_client import NeoAPI

from app.core.circuit_breaker import broker_circuit_breaker
from app.core.executors import run_blocking
from app.core.limiter import kotak_limiter
from app.core.settings import settings
from app.execution.core import BrokerAdapter
from app.notifications.manager import notification_manager

logger = logging.getLogger("KotakAdapter")

# SDK v3 REST methods never raise for validation/network failures; they return a dict
# carrying one of these keys instead (see kotak-neo-python docs/guides/MIGRATION.md §4).
_SDK_ERROR_KEYS = ("Error", "error", "Error Message")


def _sdk_error(response: Any) -> Optional[str]:
    """Returns the error text if `response` is an SDK/backend failure payload, else None."""
    if not isinstance(response, dict):
        return f"Unexpected response type: {type(response).__name__}"
    for key in _SDK_ERROR_KEYS:
        if key in response:
            return str(response[key])
    if str(response.get("stat", "")).lower() == "not_ok":
        return str(response.get("errMsg") or response.get("emsg") or response)
    return None


def _num(value: Any) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _enrich_position(pos: dict) -> dict:
    """
    v3 `positions()` returns the raw broker rows (flBuyQty, buyAmt, tok, trdSym, ...).
    Adds the derived keys the risk/strategy layers read: netQty, avgPrice, realizedPNL,
    instrumentToken, instrumentName. Formulas follow the SDK's positions.md.
    """
    buy_qty = _num(pos.get("cfBuyQty")) + _num(pos.get("flBuyQty"))
    sell_qty = _num(pos.get("cfSellQty")) + _num(pos.get("flSellQty"))
    buy_amt = _num(pos.get("cfBuyAmt")) + _num(pos.get("buyAmt"))
    sell_amt = _num(pos.get("cfSellAmt")) + _num(pos.get("sellAmt"))

    # Price factor: multiplier * (genNum/genDen) * (prcNum/prcDen); defaults to 1 for cash equity.
    factor = (_num(pos.get("multiplier")) or 1.0) * (
        ((_num(pos.get("genNum")) or 1.0) / (_num(pos.get("genDen")) or 1.0))
        * ((_num(pos.get("prcNum")) or 1.0) / (_num(pos.get("prcDen")) or 1.0))
    )
    buy_avg = buy_amt / (buy_qty * factor) if buy_qty else 0.0
    sell_avg = sell_amt / (sell_qty * factor) if sell_qty else 0.0

    net_qty = buy_qty - sell_qty
    if net_qty > 0:
        avg_price = buy_avg
    elif net_qty < 0:
        avg_price = sell_avg
    else:
        avg_price = 0.0

    closed_qty = min(buy_qty, sell_qty)
    realized = closed_qty * (sell_avg - buy_avg) * factor

    pos.setdefault("netQty", int(net_qty))
    pos.setdefault("avgPrice", round(avg_price, 4))
    pos.setdefault("realizedPNL", round(realized, 2))
    pos.setdefault("instrumentToken", pos.get("tok", ""))
    pos.setdefault("instrumentName", pos.get("trdSym", ""))
    return pos


class KotakNeoAdapter(BrokerAdapter):
    """
    Production-Ready Adapter for Kotak Neo SDK v3 (`kotakneoapi`).

    Normalizes v3 responses back to the shapes the rest of NeoPulse consumes:
    - order failures -> {"stat": "Not_Ok", "errMsg": ...}
    - positions rows gain netQty/avgPrice/realizedPNL/instrumentToken/instrumentName
    - limits gains a lowercase "net" key
    """

    def __init__(self):
        # v3 changed positional order — always use keywords.
        self.client = NeoAPI(consumer_key=settings.NEO_CONSUMER_KEY, environment=settings.NEO_ENVIRONMENT)
        self.is_logged_in = False

    async def login(self):
        """2FA Login flow with Circuit Breaker protection."""
        if self.is_logged_in:
            return

        try:
            await broker_circuit_breaker.call(self._login_sync)
            self.is_logged_in = True
            logger.info(f"✅ Kotak Neo Session Active for {settings.NEO_UCC}")
        except Exception as e:
            logger.critical(f"❌ Login Critical Failure: {e}")
            raise

    def _login_sync(self):
        """Blocking login call (runs in executor). v3 returns error dicts instead of raising."""
        totp = pyotp.TOTP(settings.NEO_TOTP_SEED).now()

        resp = self.client.totp_login(mobile_number=settings.NEO_MOBILE, ucc=settings.NEO_UCC, totp=totp)
        if not isinstance(resp, dict) or not isinstance(resp.get("data"), dict):
            raise ConnectionError(f"TOTP login failed: {resp}")

        resp = self.client.totp_validate(mpin=settings.NEO_MPIN)
        if not isinstance(resp, dict) or not isinstance(resp.get("data"), dict):
            raise ConnectionError(f"TOTP validate failed: {resp}")

    @kotak_limiter.limit
    async def place_order(self, order_params: dict) -> dict:
        """
        Executes order on Exchange.
        v3 accepts only canonical codes: segment nse_cm/bse_cm/..., order_type L/MKT/SL/SL-M,
        validity DAY/IOC, and requires price > 0 for L/SL.
        """
        try:
            response = await run_blocking(
                self.client.place_order,
                exchange_segment=order_params.get("exchange_segment", "nse_cm"),
                product=order_params.get("product", "MIS"),
                price=str(order_params.get("price", "0")),
                order_type=order_params.get("order_type", "MKT"),
                quantity=str(order_params.get("quantity")),
                validity=order_params.get("validity", "DAY"),
                trading_symbol=order_params.get("trading_symbol"),
                transaction_type=order_params.get("transaction_type"),
                amo="NO",
            )
        except Exception as e:
            logger.error(f"❌ Broker Order Failed: {e}")
            return {"stat": "Not_Ok", "errMsg": str(e)}

        error = _sdk_error(response)
        if error:
            logger.error(f"❌ Broker Order Rejected: {error}")
            # Structured error the Execution Engine parses
            return {"stat": "Not_Ok", "errMsg": error, "raw": str(response)}

        await notification_manager.notify_trade(
            order_params.get("trading_symbol"),
            order_params.get("transaction_type"),
            order_params.get("quantity"),
            order_params.get("price"),
            order_params.get("tag", "Strategy"),
        )
        return response

    async def cancel_order(self, order_id: str) -> dict:
        return await run_blocking(self.client.cancel_order, order_id=order_id)

    async def get_positions(self) -> dict:
        response = await run_blocking(self.client.positions)
        error = _sdk_error(response)
        if error:
            logger.error(f"❌ Positions fetch failed: {error}")
            return {"stat": "Not_Ok", "errMsg": error, "data": []}

        rows = response.get("data") or []
        response["data"] = [_enrich_position(p) for p in rows if isinstance(p, dict)]
        return response

    async def get_limits(self) -> dict:
        response = await run_blocking(self.client.limits)
        error = _sdk_error(response)
        if error:
            logger.error(f"❌ Limits fetch failed: {error}")
            return {"stat": "Not_Ok", "errMsg": error}

        # v3 returns "Net" (capitalized); callers read "net".
        if "net" not in response and "Net" in response:
            response["net"] = response["Net"]
        return response

    async def get_holdings(self) -> dict:
        return await run_blocking(self.client.holdings)

    async def modify_order(self, order_id: str, price: float, order_type: str, quantity: int, validity="DAY") -> dict:
        return await run_blocking(
            self.client.modify_order,
            order_id=order_id,
            price=str(price),
            order_type=order_type,
            quantity=str(quantity),
            validity=validity,
        )


kotak_adapter = KotakNeoAdapter()
