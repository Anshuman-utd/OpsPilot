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

    # Observability Integration Settings
    PROMETHEUS_URL: str = "http://localhost:9090"
    PROMETHEUS_TIMEOUT_SECONDS: float = 5.0
    GRAFANA_URL: str = "http://localhost:3001"
    TEMPO_URL: str = "http://localhost:3200"
    LOKI_URL: str = "http://localhost:3100"

    # Deterministic Incident Detection Settings
    INCIDENT_DETECTION_ENABLED: bool = True
    INCIDENT_EVALUATION_INTERVAL_SECONDS: int = 30

    # Threshold configurations for initial rules
    HTTP_5XX_HIGH_THRESHOLD: float = 0.05
    HTTP_5XX_CRITICAL_THRESHOLD: float = 0.20
    P95_LATENCY_HIGH_SECONDS: float = 1.0
    P95_LATENCY_CRITICAL_SECONDS: float = 3.0
    PAYMENT_FAILURE_HIGH_THRESHOLD: float = 20.0
    PAYMENT_FAILURE_CRITICAL_THRESHOLD: float = 50.0
    ORDER_FAILURE_HIGH_THRESHOLD: float = 20.0
    ORDER_FAILURE_CRITICAL_THRESHOLD: float = 50.0
    DB_POOL_HIGH_THRESHOLD: float = 75.0
    DB_POOL_CRITICAL_THRESHOLD: float = 90.0

    # Minimum sample gates to prevent false alerts on low traffic
    MIN_TRAFFIC_REQUESTS: int = 5
    MIN_PAYMENT_SAMPLES: int = 5

    # Safe auto-recovery: consecutive healthy cycles required to resolve (flapping prevention)
    CONSECUTIVE_HEALTHY_CYCLES_FOR_RECOVERY: int = 1

    # LLM Settings (Phase 4 Agentic Investigation)
    LLM_PROVIDER: str = "groq"
    LLM_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_API_KEY: str | None = None
    LLM_TEMPERATURE: float = 0.0
    LLM_TIMEOUT_SECONDS: float = 45.0
    LLM_MAX_RETRIES: int = 2

    # Autonomous Investigation Settings (Single-pass deterministic LangGraph pipeline)
    INVESTIGATION_ENABLED: bool = True
    AUTO_INVESTIGATION_ENABLED: bool = False
    INVESTIGATION_MAX_CONCURRENT: int = 3

    # Phase 5: Controlled Remediation & Recovery Settings
    REMEDIATION_ENABLED: bool = True
    REMEDIATION_APPROVAL_EXPIRY_MINUTES: int = 30
    REMEDIATION_EXECUTION_TIMEOUT_SECONDS: float = 30.0
    REMEDIATION_VERIFICATION_INTERVAL_SECONDS: int = 5
    REMEDIATION_VERIFICATION_TIMEOUT_SECONDS: int = 120
    REMEDIATION_CONSECUTIVE_HEALTHY_REQUIRED: int = 3
    REMEDIATION_MAX_CONCURRENT_EXECUTIONS: int = 2

    # Target Microservice Endpoints (Demo Environment)
    ORDER_SERVICE_URL: str = "http://localhost:8002"
    PAYMENT_SERVICE_URL: str = "http://localhost:8003"
    API_GATEWAY_URL: str = "http://localhost:8001"

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
