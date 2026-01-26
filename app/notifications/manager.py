import asyncio
import logging
from datetime import datetime

from app.notifications.telegram import telegram_client

logger = logging.getLogger("NotificationManager")


class NotificationManager:
    """
    Central Alert System.
    Decouples the app from specific channels (Telegram/Email/SMS).
    """

    async def push(self, message: str, level: str = "INFO"):
        """
        Sends an alert to all configured channels.

        Args:
            message: The text content
            level: INFO, SUCCESS, WARNING, ERROR, CRITICAL
        """
        # 1. Add Emojis based on severity
        icon = "ℹ️"
        if level == "SUCCESS":
            icon = "✅"
        elif level == "WARNING":
            icon = "⚠️"
        elif level == "ERROR":
            icon = "❌"
        elif level == "CRITICAL":
            icon = "🚨"

        formatted_msg = f"{icon} <b>[{level}]</b>\n{message}"

        # 2. Log Locally
        if level in ["ERROR", "CRITICAL"]:
            logger.error(message)
        else:
            logger.info(f"🔔 Alert: {message}")

        # 3. Dispatch to Channels (Fire & Forget to avoid blocking trading)
        # We wrap in create_task so the strategy doesn't wait for HTTP API
        asyncio.create_task(telegram_client.send(formatted_msg))

    async def notify_trade(self, symbol: str, signal: str, quantity: int, price: float, strategy: str):
        """Helper for standard trade alerts."""
        msg = (
            f"<b>Strategy:</b> {strategy}\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Action:</b> {signal} {quantity} @ {price}\n"
            f"<b>Time:</b> {datetime.now().strftime('%H:%M:%S')}"
        )
        await self.push(msg, level="SUCCESS")


# Global Accessor
notification_manager = NotificationManager()
