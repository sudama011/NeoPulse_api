import sys
import os
import logging

# Add project root to path
sys.path.append(os.getcwd())

from app.core.logger import setup_logging
from app.adapters.kotak.client import kotak_client

# Initialize the logger (Colored/JSON)
setup_logging()
logger = logging.getLogger("TestLogin")

def test_connection():
    logger.info("🔌 Testing Kotak Neo Connection...")
    
    try:
        # 1. Attempt Login
        kotak_client.login()
        
        # 2. Test a simple read-only call (Search)
        logger.info("🔍 Searching for 'RELIANCE' to verify session...")
        res = kotak_client.search(segment="nse_cm", symbol="RELIANCE")
        
        if res and len(res) > 0:
             logger.info(f"✅ Success! Found: {res[0]['pTrdSymbol']} (Token: {res[0]['pSymbol']})")
        else:
             logger.warning("⚠️ Session active, but no search results found.")
        
    except Exception as e:
        logger.error(f"❌ TEST FAILED: {e}")

if __name__ == "__main__":
    test_connection()