"""
core/config.py
──────────────
Single source of truth for all environment variables.
Pydantic-settings validates and type-checks every value at startup.
The app crashes immediately if a required variable is missing — fail fast.
"""
from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────────
    app_env:     str = "production"
    app_version: str = "2.0.0"
    log_level:   str = "INFO"
    port:        int = 8000
    notification_icon_color: str = "#C9A84C"   # GoldPrimary — override in .env if branding changes

    # ── Security ──────────────────────────────────────────────────────────────
    # SCRAPER_SECRET protects the /admin/* endpoints.
    # Generate it with: python -c "import secrets; print(secrets.token_hex(32))"
    scraper_secret: str

    # ALLOWED_ORIGINS: who can call this API.
    # For a mobile app (Android) CORS is not enforced by the OS — but set it
    # to your Railway URL so browser-based admin tools are restricted.
    # Format: comma-separated URLs, no trailing slash.
    # If you have no domain yet, use your Railway URL:
    #   e.g. https://grab-gully-scraper.up.railway.app
    # For local dev you can set: http://localhost:3000
    # REQUIRED — no default. App fails fast at startup if unset.
    # Format: comma-separated URLs, no trailing slash.
    # Example: https://grab-gully-scraper.up.railway.app
    # For local dev: http://localhost:3000
    allowed_origins: str

    @property
    def origins_list(self) -> List[str]:
        if self.allowed_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    # ── Supabase ──────────────────────────────────────────────────────────────
    supabase_url:         str   # https://xxxx.supabase.co
    supabase_service_key: str   # Service role key — NEVER the anon key
    supabase_jwt_secret:  str   # Supabase Dashboard → Settings → API → JWT Secret

    # ── Redis (Upstash) ───────────────────────────────────────────────────────
    upstash_redis_url:   str
    upstash_redis_token: str

    # ── Amazon Creator API (OAuth2 LWA) ───────────────────────────────────────
    # Replaces PA-API 5.0. Register at affiliate.amazon.in → Tools → Creator API.
    amazon_client_id:     str = ""
    amazon_client_secret: str = ""
    amazon_partner_tag:   str = "grabgully-21"   # Your Associates tracking ID

    # ── CueLink (Android SDK — no backend key needed) ─────────────────────────
    # CueLink affiliate conversion happens CLIENT-SIDE in the Android app.
    # The CueLink Channel ID lives in AndroidManifest.xml of the Android project.
    # Nothing to configure here on the backend.

    # ── Firebase (FCM push notifications) ────────────────────────────────────
    # firebase_project_id: Your Firebase project ID (e.g. grab-gully-android)
    # firebase_service_account_json: The full JSON content from the service account
    #   key file, on a SINGLE LINE with all inner quotes escaped.
    #   See .env.example for step-by-step instructions.
    firebase_project_id:              str = ""
    firebase_service_account_json:    str = "{}"

    # ── Scraper Behaviour ─────────────────────────────────────────────────────
    scrape_interval_minutes:          int   = 30
    scrape_start_hour:                int   = 6
    scrape_end_hour:                  int   = 23
    request_delay_seconds:            float = 4.0
    max_products_per_category:        int   = 50
    price_drop_check_interval_minutes: int  = 15

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("scraper_secret")
    @classmethod
    def secret_must_be_strong(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError(
                "SCRAPER_SECRET must be at least 32 characters. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    # ── Computed properties ───────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def amazon_configured(self) -> bool:
        """True only when both Creator API credentials are present."""
        return bool(self.amazon_client_id and self.amazon_client_secret)

    @property
    def firebase_configured(self) -> bool:
        return bool(
            self.firebase_project_id
            and self.firebase_service_account_json not in ("{}", "")
        )


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — environment is read once at first call."""
    return Settings()
