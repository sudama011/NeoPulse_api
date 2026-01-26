import logging
from functools import wraps

import pyotp
from neo_api_client import NeoAPI

from app.core.settings import settings

logger = logging.getLogger(__name__)


def with_session_validation(max_retries=1):
    """
    Decorator that validates session before API calls and auto-relogins on auth failure.

    This prevents the "Authentication Time-Bomb" where session expires after 8-10 hours
    and all subsequent API calls fail silently.

    Args:
        max_retries: Number of times to retry after re-login (default: 1)

    Usage:
        @with_session_validation()
        def get_positions(self, segment="nse_cm"):
            return self.client.positions(segment=segment)
    """

    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            attempts = 0
            last_error = None

            while attempts <= max_retries:
                try:
                    # Attempt the API call
                    return func(self, *args, **kwargs)

                except Exception as e:
                    error_str = str(e).lower()
                    last_error = e

                    # Check if it's an authentication error
                    # Kotak Neo API returns various auth error messages
                    is_auth_error = any(
                        keyword in error_str
                        for keyword in [
                            "unauthorized",
                            "invalid session",
                            "session expired",
                            "authentication failed",
                            "invalid token",
                            "401",
                            "not logged in",
                            "login required",
                        ]
                    )

                    if is_auth_error and attempts < max_retries:
                        logger.warning(
                            f"🔑 Session expired detected in {func.__name__}. "
                            f"Auto-relogin attempt {attempts + 1}/{max_retries}..."
                        )

                        # Force re-login
                        self.is_logged_in = False
                        try:
                            self.login()
                            attempts += 1
                            continue  # Retry the API call
                        except Exception as login_error:
                            logger.error(f"❌ Re-login failed: {login_error}")
                            raise login_error
                    else:
                        # Not an auth error, or max retries exceeded
                        raise e

            # If we get here, all retries failed
            raise last_error

        return wrapper

    return decorator


class NeoClient:
    _instance = None

    def __init__(self):
        # Initialize the SDK

        self.client = NeoAPI(consumer_key=settings.NEO_CONSUMER_KEY, environment=settings.NEO_ENVIRONMENT)
        self.is_logged_in = False

    @classmethod
    def get_instance(cls):
        """Singleton Pattern"""
        if cls._instance is None:
            cls._instance = NeoClient()
        return cls._instance

    def login(self):
        """
        Executes the 2-Step Headless Login
        """
        if self.is_logged_in:
            return

        try:
            # 1. Generate TOTP
            totp_now = pyotp.TOTP(settings.NEO_TOTP_SEED).now()
            logger.info(f"🔐 Generated Internal TOTP: {totp_now}")

            # 2. Login (Get View Token)
            resp = self.client.totp_login(mobile_number=settings.NEO_MOBILE, ucc=settings.NEO_UCC, totp=totp_now)

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

    @with_session_validation()
    def get_scrip_master(self, segment="nse_cm"):
        """Wrapper for client.scrip_master with auto-relogin on session expiry."""
        return self.client.scrip_master(exchange_segment=segment)

    @with_session_validation()
    def subscribe(self, tokens, is_index=False, is_depth=False):
        """Wrapper for client.subscribe with auto-relogin on session expiry."""
        return self.client.subscribe(instrument_tokens=tokens, isIndex=is_index, isDepth=is_depth)

    @with_session_validation()
    def search(self, segment, symbol):
        """Wrapper for client.search_scrip with auto-relogin on session expiry."""
        return self.client.search_scrip(exchange_segment=segment, symbol=symbol)

    @with_session_validation()
    def get_positions(self, segment="nse_cm"):
        """
        Fetches current open positions from the broker.
        Auto-relogins if session expired.
        Returns a list of dictionaries.
        """
        if not self.is_logged_in:
            self.login()

        # Neo API call to get positions
        return self.client.positions(segment=segment)

    @with_session_validation()
    def get_limits(self, segment="nse_cm"):
        """
        Fetches available funds/margins.
        Auto-relogins if session expired.
        """
        if not self.is_logged_in:
            self.login()

        # Neo API call to get limits
        # Note: Returns a list of limits for different segments
        return self.client.limits(segment=segment)


# Global Accessor
neo_client = NeoClient.get_instance()
