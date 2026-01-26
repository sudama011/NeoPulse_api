import datetime
from typing import List
from zoneinfo import ZoneInfo

# --- Timezone & Market Hours ---
# We define these here so they are globally accessible without loading the Settings object
IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN_TIME = datetime.time(9, 15, 0, tzinfo=IST)
MARKET_CLOSE_TIME = datetime.time(15, 30, 0, tzinfo=IST)
SQUARE_OFF_TIME = datetime.time(15, 10, 0, tzinfo=IST)

# --- Strategy Parameters ---
ORB_WINDOW_MINUTES = 15

# --- Universe (Phase 1) ---
TARGET_SYMBOLS: List[str] = [
    "RELIANCE",
    "HDFCBANK",
    "INFY",
    "ICICIBANK",
    "TCS",
    "SBIN",
    "BHARTIARTL",
    "KOTAKBANK",
    "LT",
    "AXISBANK",
    "ITC",
    "BAJFINANCE",
    "MARUTI",
    "TATASTEEL",
    "TATAMOTORS",
]

# --- Neo API Segments ---
EXCHANGE_NSE = "nse_cm"
SEGMENT_EQUITY = "cm"
PRODUCT_INTRADAY = "MIS"
