from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""
    jwt_secret: str = "changeme"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    payment_gateway_key: str = ""
    sms_provider_key: str = ""
    email_provider_key: str = ""
    env: str = "development"

    # Brute-force protection
    max_login_attempts: int = 5
    lockout_minutes: int = 15

    # Rate limiting (req/min)
    rate_limit_citizen: int = 60
    rate_limit_service: int = 600

    # Report async threshold (rows)
    report_async_row_threshold: int = 10_000

    # Idempotency TTL (hours)
    idempotency_ttl_hours: int = 24


settings = Settings()
