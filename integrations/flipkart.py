"""
integrations/flipkart.py
─────────────────────────
DEPRECATED — Flipkart Affiliate API is no longer used.

Flipkart product data is now fetched by scrapers/flipkart.py
(Scrapling + Playwright — public website scraper).

Affiliate link conversion for Flipkart is handled CLIENT-SIDE in the
Android app via the CueLink Android SDK (com.cuelinks.sdk:link-kit:1.0.3).
The SDK auto-converts raw Flipkart URLs into tracked affiliate links
whenever a user taps on them inside the app. No backend key needed.

This stub exists only to prevent import errors.
"""
from core.logger import get_logger

log = get_logger("integrations.flipkart")


def get_flipkart():
    """No-op stub — Flipkart API removed. Data from Scrapling, affiliate via CueLink."""
    return None