"""
core/config.py
──────────────
Single source of truth for all environment variables.
Pydantic-settings validates types at startup — crash early if misconfigured.
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
    app_env: str = "production"
    app_version: str = "2.0.0"
    log_level: str = "INFO"
    port: int = 8000

    # ── Security ──────────────────────────────────────────────────────────────
    scraper_secret: str
    allowed_origins: str = "https://grabgully.com"

    @property
    def origins_list(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

    # ── Supabase ──────────────────────────────────────────────────────────────
    supabase_url: str
    supabase_service_key: str

    # ── Redis ─────────────────────────────────────────────────────────────────
    upstash_redis_url: str
    upstash_redis_token: str

    # ── Amazon PA-API ─────────────────────────────────────────────────────────
    amazon_access_key: str = ""
    amazon_secret_key: str = ""
    amazon_partner_tag: str = "grabgully-21"
    amazon_host: str = "webservices.amazon.in"
    amazon_region: str = "us-east-1"

    # ── Flipkart ──────────────────────────────────────────────────────────────
    flipkart_affiliate_id: str = ""
    flipkart_affiliate_token: str = ""

    # ── Firebase ──────────────────────────────────────────────────────────────
    firebase_project_id: str = ""
    firebase_service_account_json: str = "{}"

    # ── Scraper Behaviour ─────────────────────────────────────────────────────
    scrape_interval_minutes: int = 30
    scrape_start_hour: int = 6
    scrape_end_hour: int = 23
    request_delay_seconds: float = 4.0
    max_products_per_category: int = 50
    price_drop_check_interval_minutes: int = 15

    @field_validator("scraper_secret")
    @classmethod
    def secret_must_be_strong(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SCRAPER_SECRET must be at least 32 characters")
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def amazon_configured(self) -> bool:
        return bool(self.amazon_access_key and self.amazon_secret_key)

    @property
    def flipkart_configured(self) -> bool:
        return bool(self.flipkart_affiliate_token)


@lru_cache
def get_settings() -> Settings:
    """Cached settings — reads env once, reused everywhere."""
    return Settings()
