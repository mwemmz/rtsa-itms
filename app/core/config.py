from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Shared team Postgres. Locally: postgresql://<user>:<password>@localhost:3330/rtsa_itms
    DATABASE_URL: str = "postgresql://localhost:3330/rtsa_itms"
    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    ENVIRONMENT: str = "development"
    RUN_MIGRATIONS_ON_STARTUP: bool = False

    # --- Security -----------------------------------------------------
    # Fernet key for encryption at rest (MFA secrets, gateway payloads).
    # If empty a key is derived from SECRET_KEY.
    ENCRYPTION_KEY: str = ""
    FORCE_HTTPS: bool = False  # redirect http->https (behind Render's proxy)
    # Only enable behind a proxy that appends the real client IP to X-Forwarded-For (Render does).
    # When off, the header is ignored so clients cannot spoof their IP.
    TRUST_PROXY_HEADERS: bool = False
    CORS_ORIGINS: str = ""  # comma separated; empty = same-origin only
    SESSION_IDLE_MINUTES: int = 30
    MAX_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_MINUTES: int = 15
    PASSWORD_MIN_LENGTH: int = 8
    REQUIRE_MFA_FOR_STAFF: bool = False
    # CAPTCHA on login/registration. Empty CAPTCHA_SECRET_KEY = built-in sandbox
    # challenge (no external service needed). Set all three to use a real
    # provider: "recaptcha" | "hcaptcha" | "turnstile".
    CAPTCHA_PROVIDER: str = ""
    CAPTCHA_SITE_KEY: str = ""
    CAPTCHA_SECRET_KEY: str = ""

    # --- Notifications ------------------------------------------------
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "no-reply@rtsa.gov.zm"
    SMS_WEBHOOK_URL: str = ""  # HTTP SMS gateway; empty = log only (sandbox)
    SMS_WEBHOOK_TOKEN: str = ""

    # --- Inter-agency ---------------------------------------------------
    NATIONAL_ID_API_URL: str = ""  # empty = sandbox (format check only)
    NATIONAL_ID_API_TOKEN: str = ""

    # --- Routing -------------------------------------------------------
    # Public OSRM demo server (OpenStreetMap routing engine). Exchange our
    # intersection-graph route for real OSM road geometry in the map view.
    OSRM_API_URL: str = "https://router.project-osrm.org/route/v1/driving"
    OSRM_TIMEOUT: float = 6.0

    # --- Performance / scalability -----------------------------------
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    SLOW_REQUEST_MS: int = 500
    REPORT_CACHE_SECONDS: int = 30

    # --- Backups / DR -------------------------------------------------
    BACKUP_DIR: str = "backups"
    BACKUP_RETENTION: int = 14

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
