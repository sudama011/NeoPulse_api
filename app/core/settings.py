from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field

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