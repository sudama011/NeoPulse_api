import logging
import httpx
from app.core.settings import settings

logger = logging.getLogger(__name__)

class TelegramClient:
    """
    Adapter to communicate with the Telegram Bot API.
    Uses 'httpx' for non-blocking Async HTTP calls.
    """
    _instance = None

    def __init__(self):
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

        if not self.token or not self.chat_id:
            logger.warning("⚠️ Telegram Token/ChatID missing! Alerts will be skipped.")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = TelegramClient()
        return cls._instance

    async def send_alert(self, message: str):
        """
        Sends an HTML formatted message to the configured Chat ID.
        """
        if not self.token or not self.chat_id:
            return

        try:
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            
            # Use AsyncClient as a context manager for a single request
            # In high-frequency apps, we might keep a session open, but for alerts this is safer.
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.base_url, json=payload, timeout=5.0)
                
                if resp.status_code != 200:
                    logger.error(f"❌ Telegram Error {resp.status_code}: {resp.text}")

        except Exception as e:
            logger.error(f"⚠️ Failed to send Telegram alert: {e}")

# Global Accessor
telegram_client = TelegramClient.get_instance()