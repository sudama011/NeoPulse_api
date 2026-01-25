# app/adapters/kotak/client.py
import logging
import pyotp
from neo_api_client import NeoAPI
from app.core.settings import settings

logger = logging.getLogger(__name__)

class KotakClient:
    _instance = None

    def __init__(self):
        # Initialize the SDK
        
        self.client = NeoAPI(
            consumer_key=settings.NEO_CONSUMER_KEY,
            environment=settings.NEO_ENVIRONMENT
        )
        self.is_logged_in = False

    @classmethod
    def get_instance(cls):
        """Singleton Pattern"""
        if cls._instance is None:
            cls._instance = KotakClient()
        return cls._instance

    def login(self):
        """
        Executes the 2-Step Headless Login
        """
        if self.is_logged_in:
            return

        try:
            # 1. Generate TOTP
            if not settings.NEO_TOTP_SEED:
                raise ValueError("NEO_TOTP_SEED is missing in .env")
            
            totp_now = pyotp.TOTP(settings.NEO_TOTP_SEED).now()
            logger.info(f"🔐 Generated Internal TOTP: {totp_now}")

            # 2. Login (Get View Token)
            resp = self.client.totp_login(
                mobile_number=settings.NEO_MOBILE,
                ucc=settings.NEO_UCC, 
                totp=totp_now
            )
            
            if isinstance(resp, dict) and "error" in resp:
                 raise Exception(f"TOTP Login Failed: {resp['error']}")

            # 3. Validate MPIN (Get Session Token)
            logger.info("🔐 Validating MPIN...")
            validate_resp = self.client.totp_validate(mpin=settings.NEO_MPIN)
            
            if isinstance(validate_resp, dict) and "error" in validate_resp:
                raise Exception(f"MPIN Validation Failed: {validate_resp['error']}")
                
            self.is_logged_in = True
            logger.info(f"✅ Kotak Neo Session Established for {settings.NEO_UCC}")
            
        except Exception as e:
            logger.error(f"❌ Login Critical Failure: {e}")
            raise
    
    def get_scrip_master(self, segment="nse_cm"):
        """Wrapper for client.scrip_master"""
        return self.client.scrip_master(exchange_segment=segment)

    def subscribe(self, tokens, is_index=False, is_depth=False):
        """Wrapper for client.subscribe"""
        return self.client.subscribe(
            instrument_tokens=tokens, 
            isIndex=is_index, 
            isDepth=is_depth
        )
    
    def search(self, segment, symbol):
        """Wrapper for client.search_scrip"""
        return self.client.search_scrip(
            exchange_segment=segment,
            symbol=symbol
        )
    
    def get_positions(self, segment="nse_cm"):
        """
        Fetches current open positions from the broker.
        Returns a list of dictionaries.
        """
        if not self.is_logged_in:
            self.login()
            
        try:
            # Neo API call to get positions
            return self.client.positions(segment=segment)
        except Exception as e:
            logger.error(f"❌ Failed to fetch positions: {e}")
            return []

    def get_limits(self, segment="nse_cm"):
        """
        Fetches available funds/margins.
        """
        if not self.is_logged_in:
            self.login()

        try:
            # Neo API call to get limits
            # Note: Returns a list of limits for different segments
            return self.client.limits(segment=segment)
        except Exception as e:
            logger.error(f"❌ Failed to fetch limits: {e}")
            return {}

# Global Accessor
kotak_client = KotakClient.get_instance()