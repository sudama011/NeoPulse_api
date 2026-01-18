from __future__ import annotations

from typing import List

from pydantic import BaseSettings, Field
from zoneinfo import ZoneInfo


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    app_name: str = "NeoPulse"
    timezone: str = "Asia/Kolkata"

    neo_mobile: str
    neo_password: str
    neo_totp_secret: str
    neo_api_key: str
    neo_consumer_key: str
    neo_access_token: str | None = None

    symbols: List[str] = Field(default_factory=list)

    db_url: str = "postgresql+asyncpg://user:pass@localhost:5432/neopulse"
    max_daily_loss: float = 1000.0

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    class Config:
        env_file = ".env"
        env_prefix = "NEOPULSE_"

