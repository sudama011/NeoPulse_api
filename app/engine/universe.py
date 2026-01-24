import logging
import pandas as pd
from typing import Dict, List
from app.core.constants import TARGET_SYMBOLS, EXCHANGE_NSE

logger = logging.getLogger(__name__)

class UniverseManager:
    def __init__(self, client):
        self.client = client
        self.instrument_map: Dict[str, str] = {} # {"RELIANCE": "2885"}
        self.reverse_map: Dict[str, str] = {}    # {"2885": "RELIANCE"}

    async def load_instruments(self):
        """
        Fetches the 'Scrip Master' from Kotak and filters for our Target Symbols.
        """
        try:
            logger.info("Downloading Scrip Master...")
            # Kotak SDK method to search instruments
            # Note: In production, we might download the full CSV for speed, 
            # but for <20 stocks, search_instrument is faster.
            
            for symbol in TARGET_SYMBOLS:
                # Search for the symbol in NSE Equity
                results = self.client.search_scrip(
                    exchange_segment=EXCHANGE_NSE, 
                    symbol=symbol + "-EQ" # Kotak usually requires suffix like -EQ
                )
                
                if results and len(results) > 0:
                    # Parse the correct token
                    # Assuming results[0] is the best match
                    token = results[0]['pSymbol'] # Check SDK docs for exact field name 'pSymbol' or 'instToken'
                    self.instrument_map[symbol] = token
                    self.reverse_map[token] = symbol
                    logger.debug(f"Mapped {symbol} -> {token}")
                else:
                    logger.error(f"Could not find token for {symbol}")

            logger.info(f"Universe Loaded: {len(self.instrument_map)} instruments ready.")
            return self.instrument_map

        except Exception as e:
            logger.critical(f"Failed to load universe: {str(e)}")
            raise e

    def get_token(self, symbol: str) -> str:
        return self.instrument_map.get(symbol)

    def get_symbol(self, token: str) -> str:
        return self.reverse_map.get(token)