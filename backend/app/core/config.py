from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized application settings validated via Pydantic v2."""

    # Application settings
    APP_NAME: str = "OpsPilot"
    APP_VERSION: str = "0.1.0"
    APP_ENV: str = "development"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # PostgreSQL async connection URL
    DATABASE_URL: str = "postgresql+asyncpg://opspilot:opspilot_dev@localhost:5433/opspilot"

    # Redis connection URL
    REDIS_URL: str = "redis://localhost:6380/0"

    # LLM Settings (Future roadmap - not required to initialize in Phase 1)
    LLM_PROVIDER: str = "groq"
    LLM_MODEL: str | None = "llama-3.3-70b-versatile"
    GROQ_API_KEY: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()
