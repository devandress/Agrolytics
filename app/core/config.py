"""Application configuration loaded from environment variables."""


from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Insecure placeholder values that must never reach production.
_INSECURE_JWT_SECRETS = {
    "change-me-in-production",
    "change-me-generate-with-openssl-rand-hex-32",
}


class Settings(BaseSettings):
    """Central settings object; values come from environment / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database connection URLs (async for FastAPI, sync for Celery/Alembic)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@db:5432/agrolytics"
    DATABASE_URL_SYNC: str = "postgresql://postgres:postgres@db:5432/agrolytics"

    # Connection-pool sizing. Defaults are deliberately small so the total stays
    # within free/managed Postgres connection caps (e.g. Supabase free ≈ 60).
    # Raise these via env once on a larger plan. Total ≈ (async pool+overflow) +
    # (sync pool+overflow) per process — keep the sum under your provider's limit.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE: int = 1800
    SYNC_DB_POOL_SIZE: int = 2
    SYNC_DB_MAX_OVERFLOW: int = 3

    # JWT token configuration
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Redis broker for Celery task queue
    REDIS_URL: str = "redis://redis:6379/0"

    # Satellite data source (Planetary Computer STAC API)
    STAC_API_URL: str = "https://planetarycomputer.microsoft.com/api/stac/v1"
    PLANETARY_COMPUTER_KEY: str | None = None

    # Local file storage for COG rasters and data
    DATA_DIR: str = "/app/data"
    S3_BUCKET: str = "agrolytics-cog"

    # ── Database backups (S3-compatible object storage — Cloudflare R2, etc.) ──
    # Empty endpoint = backups are a no-op (logged, not silently skipped) rather
    # than failing — same pattern as DEEPSEEK_API_KEY / MERCADOPAGO_ACCESS_TOKEN.
    # Supabase's free tier has NO point-in-time recovery; this is the substitute.
    BACKUP_S3_ENDPOINT_URL: str = ""
    BACKUP_S3_BUCKET: str = ""
    BACKUP_S3_ACCESS_KEY: str = ""
    BACKUP_S3_SECRET_KEY: str = ""
    BACKUP_RETENTION_DAYS: int = 14

    # Optional payment/API integrations
    DEEPSEEK_API_KEY: str = ""

    # MercadoPago (Checkout Pro). Empty = billing runs in preview-only mode (no
    # real charge, no init_point). Get TEST-... credentials from
    # https://www.mercadopago.com.mx/developers/panel/app for sandbox testing;
    # switch to live APP_USR-... credentials only when ready to charge for real.
    MERCADOPAGO_ACCESS_TOKEN: str = ""
    MERCADOPAGO_PUBLIC_KEY: str = ""
    # Public origin used to build MercadoPago back_urls + webhook notification_url,
    # and password-reset links.
    PUBLIC_BASE_URL: str = "http://localhost:8001"

    # ── Transactional email (password reset). Empty host = no-op: the reset link
    # is logged instead of emailed (loud, not silent) — fine for local dev, not
    # for production. Any SMTP provider works (SES, Resend's SMTP endpoint, etc).
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "Agrolytics <no-reply@agrolytics.app>"

    # Application environment: development | staging | production
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # ── Security ──────────────────────────────────────────────────────────────
    # Comma-separated list of origins allowed by CORS. Defaults to local dev only;
    # set explicit production origins via the CORS_ORIGINS env var.
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8000"
    # Per-IP rate limit for auth endpoints (slowapi syntax, e.g. "5/minute").
    AUTH_RATE_LIMIT: str = "5/minute"
    # Per-IP rate limit for AI endpoints (DeepSeek chat + task proposals) — the
    # actual per-plan monthly quota (PLANS[...]["ai_monthly"]) isn't metered yet,
    # this is just a floor against runaway loops / abuse driving up token cost.
    AI_RATE_LIMIT: str = "20/hour"
    # Hide interactive API docs (/docs, /redoc) when running in production.
    DOCS_ENABLED: bool = True

    # ── Observability & analytics (all optional; off when empty) ────────────────
    SENTRY_DSN: str = ""                              # error tracking (backend)
    POSTHOG_KEY: str = ""                             # analytics (public project key)
    POSTHOG_HOST: str = "https://us.i.posthog.com"    # analytics host

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    @property
    def cors_origins_list(self) -> list[str]:
        """Parsed CORS allowlist (comma-separated CORS_ORIGINS)."""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @model_validator(mode="after")
    def _normalize_database_urls(self) -> "Settings":
        """Accept a single managed connection string and derive both driver URLs.

        Managed Postgres providers (Render, Heroku, Supabase) emit a plain
        ``postgres://`` / ``postgresql://`` URL. We normalize the legacy
        ``postgres://`` scheme and, when only the sync URL is customised, derive
        the async (asyncpg) URL from it so a single env var is enough.
        """
        def _sync(url: str) -> str:
            return url.replace("postgres://", "postgresql://", 1) if url.startswith("postgres://") else url

        def _async(url: str) -> str:
            url = _sync(url)
            if "+asyncpg" not in url and url.startswith("postgresql://"):
                return url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url

        default_sync = "postgresql://postgres:postgres@db:5432/agrolytics"
        self.DATABASE_URL_SYNC = _sync(self.DATABASE_URL_SYNC)

        # If the async URL was left at its default but a custom sync URL was given,
        # derive the async URL from the sync one (single-var managed-DB setups).
        if self.DATABASE_URL.startswith("postgresql+asyncpg://postgres:postgres@db") \
                and self.DATABASE_URL_SYNC != default_sync:
            self.DATABASE_URL = _async(self.DATABASE_URL_SYNC)
        else:
            self.DATABASE_URL = _async(self.DATABASE_URL)
        return self

    @model_validator(mode="after")
    def _enforce_production_security(self) -> "Settings":
        """Fail fast on insecure configuration when APP_ENV=production."""
        if not self.is_production:
            return self

        errors: list[str] = []
        if self.JWT_SECRET in _INSECURE_JWT_SECRETS or len(self.JWT_SECRET) < 32:
            errors.append(
                "JWT_SECRET must be a strong 32+ char secret in production "
                "(generate with `openssl rand -hex 32`)."
            )
        if ":postgres@" in self.DATABASE_URL or ":postgres@" in self.DATABASE_URL_SYNC:
            errors.append("DATABASE_URL uses the default 'postgres' password in production.")
        if "*" in self.cors_origins_list:
            errors.append("CORS_ORIGINS must not be '*' in production.")

        if errors:
            raise ValueError("Insecure production configuration:\n  - " + "\n  - ".join(errors))
        return self


settings = Settings()

