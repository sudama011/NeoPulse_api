import pandas as pd
import yfinance as yf
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("BacktestFeed")

class HistoricalFeed:
    """
    The 'Time Machine' for Data.
    Fetches historical data and streams it candle-by-candle.
    """
    
    def __init__(self, symbol: str, days: int = 30, interval: str = "5m"):
        self.symbol = symbol
        self.days = days
        self.interval = interval
        self.data = pd.DataFrame()

    def load_data(self):
        """Fetches data from Yahoo Finance or CSV."""
        logger.info(f"⏳ Fetching {self.days} days of {self.interval} data for {self.symbol}...")
        
        # Adjust symbol for Yahoo (e.g., RELIANCE.NS)
        ticker = f"{self.symbol}.NS" if not self.symbol.endswith(".NS") else self.symbol
        
        try:
            end = datetime.now()
            start = end - timedelta(days=self.days)
            df = yf.download(ticker, start=start, end=end, interval=self.interval, progress=False)
            
            if df.empty:
                logger.error("❌ No data received from Yahoo Finance.")
                return

            # Flatten MultiIndex columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Rename to match our internal schema
            df = df.rename(columns={
                "Open": "open", "High": "high", "Low": "low", 
                "Close": "close", "Volume": "volume"
            })
            
            # Ensure timestamps are standard
            df.index = pd.to_datetime(df.index)
            self.data = df
            logger.info(f"✅ Loaded {len(df)} historical candles.")
            
        except Exception as e:
            logger.error(f"❌ Data Load Error: {e}")

    def stream(self):
        """Generator that yields one candle at a time."""
        for timestamp, row in self.data.iterrows():
            yield {
                "start_time": timestamp,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"]
            }