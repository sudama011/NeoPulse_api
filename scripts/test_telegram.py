import asyncio
import sys
import os

# 1. Fix Path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
sys.path.insert(0, project_root)

# 2. Setup Logger
from app.core.logger import setup_logging
setup_logging()

from app.adapters.telegram.client import telegram_client

async def main():
    print("--- 📡 Testing Telegram Connection ---")
    
    # Debug: Print first few chars of token to verify it loaded
    token = telegram_client.token
    chat_id = telegram_client.chat_id
    
    if not token:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN is empty in .env")
        return
    if not chat_id:
        print("❌ ERROR: TELEGRAM_CHAT_ID is empty in .env")
        return
        
    print(f"🔹 Token Loaded: {token[:5]}*******")
    print(f"🔹 Chat ID: {chat_id}")

    # Send Message
    print("🔹 Sending test message...")
    
    # We AWAIT here to ensure the script waits for the response
    await telegram_client.send_alert("<b>🚀 NeoPulse Test Message</b>\nSystem is Online!")
    
    print("✅ Message request sent. Check your phone!")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())