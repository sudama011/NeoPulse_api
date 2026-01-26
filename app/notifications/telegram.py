import logging
import httpx
from app.core.settings import settings

logger = logging.getLogger("TelegramClient")

class TelegramClient:
    """
    Low-level adapter for Telegram Bot API.
    """

    def __init__(self):
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        self.enabled = bool(self.token and self.chat_id)
        
        if not self.enabled:
            logger.warning("⚠️ Telegram credentials missing. Alerts will be DISABLED.")

    async def send(self, message: str):
        """
        Sends raw text to Telegram.
        """
        if not self.enabled: return

        try:
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            
            async with httpx.AsyncClient() as client:
                resp = await client.post(self.base_url, json=payload, timeout=5.0)
                if resp.status_code != 200:
                    logger.error(f"❌ Telegram Send Failed: {resp.text}")

        except Exception as e:
            logger.error(f"⚠️ Telegram Connection Error: {e}")

# Internal Accessor
telegram_client = TelegramClient()