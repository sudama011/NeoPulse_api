from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field
from typing import ClassVar, List
import datetime
from zoneinfo import ZoneInfo

class Settings(BaseSettings):
    # --- Project Info ---
    APP_NAME: str = "NeoPulse"
    ENV: str = "dev"  # dev, prod
    LOG_LEVEL: str = "INFO"

    # --- Database (AsyncPG) ---
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "neopulse_db"

    # --- Security ---
    SECRET_KEY: str
    ENCRYPTION_KEY: str  # For Fernet (Kotak Creds)

    # --- Kotak Neo Credentials ---
    NEO_ENVIRONMENT: str = "prod"
    NEO_CONSUMER_KEY: str
    NEO_UCC: str
    NEO_MOBILE: str
    NEO_MPIN: str
    NEO_TOTP_SEED: str

    PAPER_TRADING: bool = True
    
    # --- Telegram Control Center ---
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: int  # Your User ID (security gate)

    # --- Risk & Capital (The "Two-Bullet" Logic) ---
    MAX_CAPITAL_ALLOCATION: float = 10000.0
    MAX_CONCURRENT_TRADES: int = 3
    LEVERAGE_MULTIPLIER: float = 5.0
    MAX_DAILY_LOSS: float = 1000.0

    # --- Constants (Not loaded from .env) ---
    # We use ClassVar so Pydantic ignores these during .env validation
    IST: ClassVar[ZoneInfo] = ZoneInfo("Asia/Kolkata")
    
    # Market Hours
    MARKET_OPEN_TIME: ClassVar[datetime.time] = datetime.time(9, 15, 0, tzinfo=IST)
    MARKET_CLOSE_TIME: ClassVar[datetime.time] = datetime.time(15, 30, 0, tzinfo=IST)
    SQUARE_OFF_TIME: ClassVar[datetime.time] = datetime.time(15, 10, 0, tzinfo=IST)
    
    # Strategy
    ORB_WINDOW_MINUTES: ClassVar[int] = 15

    # Universe (Phase 1)
    TARGET_SYMBOLS: List[str] = [
        "RELIANCE", "HDFCBANK", "INFY", "ICICIBANK", "TCS", 
        "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK",
        "ITC", "BAJFINANCE", "MARUTI", "TATASTEEL", "TATAMOTORS"
    ]

    # Neo API Segments
    EXCHANGE_NSE: ClassVar[str] = "nse_cm"
    SEGMENT_EQUITY: ClassVar[str] = "cm"
    PRODUCT_INTRADAY: ClassVar[str] = "MIS"

    # --- Computed Fields ---
    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # Pydantic V2 Configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"  # Ignore extra fields in .env file
    )

settings = Settings()