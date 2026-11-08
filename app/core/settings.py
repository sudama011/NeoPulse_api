from datetime import date
from typing import Optional

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Project ---
    APP_NAME: str = "NeoPulse"
    ENV: str = "dev"
    LOG_LEVEL: str = "INFO"
    PAPER_TRADING: bool = True

    # --- Database (asyncpg) ---
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "neopulse_db"

    # --- Kotak Neo ---
    NEO_ENVIRONMENT: str = "prod"
    NEO_CONSUMER_KEY: str
    NEO_UCC: str
    NEO_MOBILE: str
    NEO_MPIN: str
    NEO_TOTP_SEED: str

    # --- Telegram (optional) ---
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    # --- Risk (account level) ---
    MAX_DAILY_LOSS: float = 2000.0  # net of charges; trips the kill switch
    MAX_OPEN_POSITIONS: int = 5
    ENTRY_CUTOFF: str = "15:00"  # no new entries after this time (IST)
    SQUARE_OFF_TIME: str = "15:15"  # flatten intraday (MIS) positions (IST)
    MARKET_HOLIDAYS: str = ""  # comma separated YYYY-MM-DD

    # --- OMS ---
    ORDER_POLL_SECONDS: float = 2.0
    LIMIT_ORDER_TTL_SECONDS: int = 60  # unfilled limit orders are cancelled after this
    PAPER_SLIPPAGE_BPS: float = 2.0

    @field_validator("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        return v or None

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def holidays(self) -> frozenset[date]:
        return frozenset(date.fromisoformat(d.strip()) for d in self.MARKET_HOLIDAYS.split(",") if d.strip())

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()  # type: ignore[call-arg]  # values come from the environment / .env
