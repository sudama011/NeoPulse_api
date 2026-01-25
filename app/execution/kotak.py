import logging
import pyotp
from neo_api_client import NeoAPI
from app.core.settings import settings
from app.core.circuit_breaker import broker_circuit_breaker
from app.core.executors import run_blocking
from app.execution.core import BrokerAdapter

logger = logging.getLogger("KotakAdapter")

class KotakNeoAdapter(BrokerAdapter):
    """
    Production-Ready Adapter for Kotak Neo API v2.
    Features: Auto-Login, Thread-Pooling, Circuit Breaking.
    """
    _instance = None

    def __init__(self):
        self.client = NeoAPI(
            consumer_key=settings.NEO_CONSUMER_KEY,
            environment=settings.NEO_ENVIRONMENT
        )
        self.is_logged_in = False

    @classmethod
    def get_instance(cls):
        if not cls._instance: cls._instance = KotakNeoAdapter()
        return cls._instance

    async def login(self):
        """2FA Login flow with Circuit Breaker protection."""
        if self.is_logged_in: return

        try:
            await broker_circuit_breaker.call(self._login_sync)
            self.is_logged_in = True
            logger.info(f"✅ Kotak Neo Session Active for {settings.NEO_UCC}")
        except Exception as e:
            logger.critical(f"❌ Login Critical Failure: {e}")
            raise

    def _login_sync(self):
        """Blocking login call (runs in executor)."""
        # 1. Generate TOTP
        totp = pyotp.TOTP(settings.NEO_TOTP_SEED).now()
        
        # 2. Login (Get View Token)
        self.client.totp_login(
            mobilenumber=settings.NEO_MOBILE,
            ucc=settings.NEO_UCC, 
            totp=totp
        )
        
        # 3. Validate MPIN (Get Session Token)
        self.client.totp_validate(mpin=settings.NEO_MPIN)

    async def place_order(self, order_params: dict) -> dict:
        """
        Executes order on Exchange.
        Wraps blocking SDK call in thread pool.
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
                amo="NO"
            )
            return response
        except Exception as e:
            logger.error(f"❌ Broker Order Failed: {e}")
            return {"stat": "Not_Ok", "errMsg": str(e)}

    async def cancel_order(self, order_id: str) -> dict:
        return await run_blocking(self.client.cancel_order, order_no=order_id)

    async def get_positions(self) -> dict:
        return await run_blocking(self.client.positions)

    async def get_limits(self) -> dict:
        return await run_blocking(self.client.limits)