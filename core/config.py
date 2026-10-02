"""Application configuration loaded from environment variables and `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


class ScrapeTarget(BaseModel):
    """A search-results URL to scrape, with optional brand/model hints.

    Hints are used when the results table does not expose brand/model columns
    (e.g. when the search is already filtered by brand in the URL).
    """

    url: str = Field(min_length=8)
    brand: str | None = None
    model: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- Infrastructure -----------------------------------------------------
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/arbitrage",
        description="Async SQLAlchemy URL (postgresql+asyncpg://...).",
    )
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # --- Notifications ------------------------------------------------------
    TELEGRAM_BOT_TOKEN: SecretStr | None = Field(default=None)
    TELEGRAM_CHAT_ID: str | None = Field(default=None)

    # --- Analysis -----------------------------------------------------------
    PRICE_DEVIATION_THRESHOLD: float = Field(default=0.10, gt=0.0, lt=1.0)
    MIN_SAMPLE_SIZE: int = Field(default=5, ge=1, description="Minimum comparable listings for a valid market average.")
    MARKET_STATS_TTL_MINUTES: int = Field(default=60, ge=0)
    OPPORTUNITY_EXPIRY_HOURS: int = Field(default=72, ge=1)

    # --- Proxies ------------------------------------------------------------
    PROXY_API_URL: str | None = Field(default=None, description="Endpoint returning a JSON list or newline list of proxy URLs.")
    PROXY_MAX_FAILURES: int = Field(default=3, ge=1)

    # --- Scraper ------------------------------------------------------------
    SCRAPER_HEADLESS: bool = True
    SCRAPER_MAX_RETRIES: int = Field(default=3, ge=1)
    SCRAPER_NAVIGATION_TIMEOUT_MS: int = Field(default=60_000, ge=5_000)
    SCRAPER_CHALLENGE_TIMEOUT_S: float = Field(default=30.0, ge=1.0)
    SCRAPE_TARGETS: list[ScrapeTarget] = Field(default_factory=list)
    SCRAPE_INTERVAL_SECONDS: int = Field(default=900, ge=30)

    # --- Logging ------------------------------------------------------------
    LOG_LEVEL: str = Field(default="INFO")
    LOG_DIR: Path = Field(default=PROJECT_ROOT / "logs")

    @field_validator("DATABASE_URL")
    @classmethod
    def _force_async_driver(cls, value: str) -> str:
        if value.startswith("postgres://"):
            value = "postgresql://" + value[len("postgres://"):]
        if value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value[len("postgresql://"):]
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg driver")
        return value

    @field_validator("LOG_LEVEL")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        level = value.upper().strip()
        allowed = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return level

    @field_validator("LOG_DIR")
    @classmethod
    def _resolve_log_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
