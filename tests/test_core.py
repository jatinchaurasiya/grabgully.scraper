"""
tests/test_core.py
──────────────────
Minimal test suite for Grab Gully scraper service.

Dependencies: pytest (test runner), unittest.mock (stdlib), httpx (FastAPI TestClient).
No new packages required — httpx is already in requirements.txt.

Run with:
    pytest tests/ -v

Design notes:
- BaseScraper helpers (extract_price, extract_int, etc.) are @staticmethod,
  so they are called directly on the class — no instantiation, no env vars needed.
- TestHealthEndpoint patches core.config.get_settings at the module level so
  FastAPI startup (CORS, Supabase client, scheduler) never reads real env vars.
"""
from __future__ import annotations
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# ── Make sure the project root is on sys.path ──────────────────────────────────
# Needed when pytest is run from the repo root: `pytest tests/ -v`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers imported directly (no env vars needed — these are static methods) ──
from scrapers.base import BaseScraper
from integrations.affiliate import build_amazon_affiliate_url, build_affiliate_url


# =============================================================================
#  class TestBaseScraper
# =============================================================================

class TestBaseScraper:
    """
    Unit tests for BaseScraper static utility methods.
    Called on the class directly — no instantiation so no Settings dependency.
    """

    # ── extract_price ─────────────────────────────────────────────────────────

    def test_extract_price_indian_rupee(self):
        """₹ symbol + Indian comma format → float."""
        assert BaseScraper.extract_price("₹1,299") == 1299.0

    def test_extract_price_with_rs(self):
        """'Rs.' prefix + decimals → float."""
        assert BaseScraper.extract_price("Rs. 999.00") == 999.0

    def test_extract_price_empty(self):
        """Empty string → 0.0 (not an exception)."""
        assert BaseScraper.extract_price("") == 0.0

    def test_extract_price_none(self):
        """None input → 0.0 (not an exception)."""
        assert BaseScraper.extract_price(None) == 0.0

    def test_extract_price_plain_int(self):
        """Plain integer string → float."""
        assert BaseScraper.extract_price("2499") == 2499.0

    def test_extract_price_with_spaces(self):
        """Price with surrounding spaces still parses."""
        assert BaseScraper.extract_price("  ₹ 599  ") == 599.0

    # ── extract_int ───────────────────────────────────────────────────────────

    def test_extract_int_percentage(self):
        """'50% off' → 50."""
        assert BaseScraper.extract_int("50% off") == 50

    def test_extract_int_no_digits(self):
        """String with no digits → 0."""
        assert BaseScraper.extract_int("No discount") == 0

    def test_extract_int_empty(self):
        """Empty string → 0."""
        assert BaseScraper.extract_int("") == 0

    # ── clean_title ───────────────────────────────────────────────────────────

    def test_clean_title_whitespace(self):
        """Multiple internal spaces collapsed, leading/trailing stripped."""
        assert BaseScraper.clean_title("  Samsung   Galaxy  ") == "Samsung Galaxy"

    def test_clean_title_empty_string(self):
        """Empty string → empty string (no crash)."""
        assert BaseScraper.clean_title("") == ""

    def test_clean_title_newlines(self):
        """Newlines and tabs are treated as whitespace."""
        assert BaseScraper.clean_title("Apple\n Watch\t Series") == "Apple Watch Series"

    # ── safe_url ──────────────────────────────────────────────────────────────

    def test_safe_url_relative(self):
        """Relative path → prepended with base URL."""
        assert BaseScraper.safe_url("/product/123", "https://myntra.com") == \
               "https://myntra.com/product/123"

    def test_safe_url_absolute(self):
        """Absolute http URL is returned unchanged (base is ignored)."""
        assert BaseScraper.safe_url("https://myntra.com/p", "base") == \
               "https://myntra.com/p"

    def test_safe_url_protocol_relative(self):
        """Protocol-relative URL (//cdn...) → prefixed with https:."""
        assert BaseScraper.safe_url("//cdn.img/x.jpg", "") == \
               "https://cdn.img/x.jpg"

    def test_safe_url_empty_string(self):
        """Empty URL → empty string (no crash)."""
        assert BaseScraper.safe_url("", "https://example.com") == ""

    def test_safe_url_base_trailing_slash_handled(self):
        """Base with trailing slash + relative path — no double slash."""
        result = BaseScraper.safe_url("/item/42", "https://meesho.com/")
        assert "//" not in result.replace("https://", "")


# =============================================================================
#  class TestAffiliateBuilder
# =============================================================================

class TestAffiliateBuilder:
    """
    Unit tests for integrations/affiliate.py.
    build_amazon_affiliate_url reads AMAZON_PARTNER_TAG from Settings.
    We patch get_settings so no real env vars are needed.
    """

    @pytest.fixture(autouse=True)
    def mock_settings(self):
        """Provide a fake Settings object for every test in this class."""
        fake = MagicMock()
        fake.amazon_partner_tag = "grabgully-21"
        with patch("integrations.affiliate.get_settings", return_value=fake):
            yield fake

    # ── build_amazon_affiliate_url ────────────────────────────────────────────

    def test_amazon_affiliate_url_contains_tag(self):
        """Affiliate URL must carry the Associates partner tag."""
        url = build_amazon_affiliate_url("B08XYZ123")
        assert "grabgully-21" in url

    def test_amazon_affiliate_url_contains_asin(self):
        """Affiliate URL must embed the ASIN."""
        url = build_amazon_affiliate_url("B08XYZ123")
        assert "B08XYZ123" in url

    def test_amazon_affiliate_url_is_amazon_domain(self):
        """URL must point to amazon.in."""
        url = build_amazon_affiliate_url("B08XYZ123")
        assert "amazon.in" in url

    # ── build_affiliate_url dispatcher ────────────────────────────────────────

    def test_build_affiliate_url_amazon_dispatcher(self):
        """Dispatcher routes 'amazon' correctly → amazon.in URL."""
        result = build_affiliate_url("amazon", "", "B08XYZ123")
        assert "amazon.in" in result

    def test_build_affiliate_url_flipkart_returns_url(self):
        """Flipkart → raw URL passthrough (CueLink SDK handles affiliate side)."""
        raw = "https://flipkart.com/p/test"
        result = build_affiliate_url("flipkart", raw)
        assert result == raw

    def test_build_affiliate_url_myntra_passthrough(self):
        """Myntra → raw URL passthrough."""
        raw = "https://myntra.com/product/987"
        assert build_affiliate_url("myntra", raw) == raw

    def test_build_affiliate_url_unknown_platform_passthrough(self):
        """Unknown platform → raw URL returned unchanged (safe fallback)."""
        raw = "https://example.com/product"
        assert build_affiliate_url("unknown_platform", raw) == raw


# =============================================================================
#  class TestHealthEndpoint
# =============================================================================

# Build a fake Settings object that satisfies all required fields.
# This is constructed ONCE and reused for all health-check tests.
def _make_fake_settings() -> MagicMock:
    s = MagicMock()
    s.app_env            = "test"
    s.app_version        = "2.0.0"
    s.log_level          = "INFO"
    s.port               = 8000
    s.scraper_secret     = "a" * 32          # passes the 32-char validator
    s.allowed_origins    = "http://localhost:3000"
    s.origins_list       = ["http://localhost:3000"]
    s.supabase_url       = "https://test.supabase.co"
    s.supabase_service_key = "test-service-key"
    s.supabase_jwt_secret  = "test-jwt-secret"
    s.upstash_redis_url    = "https://test.upstash.io"
    s.upstash_redis_token  = "test-token"
    s.amazon_partner_tag   = "grabgully-21"
    s.amazon_client_id     = ""
    s.amazon_client_secret = ""
    s.amazon_configured    = False
    s.firebase_project_id  = ""
    s.firebase_service_account_json = "{}"
    s.firebase_configured  = False
    s.scrape_start_hour    = 6
    s.scrape_end_hour      = 23
    s.request_delay_seconds = 4.0
    s.max_products_per_category = 50
    s.price_drop_check_interval_minutes = 15
    s.scrape_interval_minutes = 30
    s.is_production = False
    return s


class TestHealthEndpoint:
    """
    Integration-style test for GET /health using FastAPI TestClient.

    All external dependencies (Supabase, Redis, APScheduler, Firebase)
    are mocked out so the test runs with zero env vars and zero network I/O.
    """

    @pytest.fixture(autouse=True)
    def patch_all_externals(self):
        """
        Patch every external call that main.py triggers at import/startup time.
        The patches must be applied BEFORE `from main import app` runs,
        so we patch at the module level using the with-statement context.
        """
        fake_settings = _make_fake_settings()

        # Supabase client — prevent real HTTP connections
        fake_db = MagicMock()
        fake_db.table.return_value.select.return_value.limit.return_value\
               .execute.return_value.data = [{"id": "ok"}]

        # Redis / cache — prevent real HTTP connections
        fake_cache = MagicMock()

        with (
            patch("core.config.get_settings", return_value=fake_settings),
            patch("services.db.get_db",       return_value=fake_db),
            patch("services.cache.get_cache",  return_value=fake_cache),
            patch("core.scheduler.create_scheduler", return_value=MagicMock()),
            # Prevent APScheduler from starting a real background thread
            patch("apscheduler.schedulers.asyncio.AsyncIOScheduler.start"),
        ):
            # Import app inside the patch context so all module-level singletons
            # (Supabase client, scheduler, etc.) see the mocked dependencies.
            from fastapi.testclient import TestClient
            # Force re-import in case main is already cached
            import importlib
            import main as main_module
            importlib.reload(main_module)
            self.client = TestClient(main_module.app, raise_server_exceptions=False)
            yield

    # ── Tests ─────────────────────────────────────────────────────────────────

    def test_health_returns_200(self):
        """GET /health must return HTTP 200."""
        response = self.client.get("/health")
        assert response.status_code == 200

    def test_health_has_status_key(self):
        """GET /health JSON body must contain a 'status' key."""
        response = self.client.get("/health")
        body = response.json()
        assert "status" in body

    def test_health_status_value_is_string(self):
        """The 'status' value should be a non-empty string."""
        response = self.client.get("/health")
        body = response.json()
        assert isinstance(body["status"], str)
        assert len(body["status"]) > 0
