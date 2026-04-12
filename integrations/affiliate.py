"""
integrations/affiliate.py
──────────────────────────
Affiliate URL builders for every platform.

Strategy:
  - Amazon → Direct Associates deep link  (https://www.amazon.in/dp/{ASIN}?tag=...)
  - All others (Flipkart, Myntra, Meesho, Ajio, Snapdeal) → CueLink short-link API
    CueLink wraps any product URL and returns a trackable affiliate short-link.
    Docs: https://cuelinks.com/app/api-documentation

All Buy buttons in the Android app route through:
  GET /go/{platform}/{listing_id}
which calls log_affiliate_click() then redirects to the URL built here.

IMPORTANT: These URLs contain your affiliate tracking parameters.
Never expose them in the Android APK — only serve from the backend.
"""
from __future__ import annotations
import asyncio
from typing import Optional
from urllib.parse import urlencode

import httpx

from core.config import get_settings
from core.logger import get_logger

log = get_logger("affiliate")

# CueLink API endpoint
CUELINK_API_URL = "https://cl.cuelinks.com/api/v1/generate-link"


# ─── Amazon ───────────────────────────────────────────────────────────────────

def build_amazon_affiliate_url(asin: str) -> str:
    """
    Build a standard Amazon India affiliate URL from an ASIN.
    Format: https://www.amazon.in/dp/{ASIN}?tag={partner_tag}&linkCode=ogi
    No CueLink needed — Amazon has its own Associates deep-link format.
    """
    tag = get_settings().amazon_partner_tag
    return f"https://www.amazon.in/dp/{asin}?tag={tag}&linkCode=ogi&th=1&psc=1"


# ─── CueLink (Flipkart, Myntra, Meesho, Ajio, Snapdeal) ─────────────────────

async def build_cuelink_url(product_url: str) -> str:
    """
    Convert any product URL into a CueLink affiliate short-link.

    CueLink automatically detects the merchant (Flipkart, Myntra, etc.)
    from the URL and applies your registered affiliate tracking.

    Args:
        product_url: Original product page URL from the scraper.

    Returns:
        CueLink short affiliate URL on success,
        or the original url if CueLink is not configured / request fails.
    """
    s = get_settings()
    if not s.cuelink_api_key or not product_url:
        return product_url

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                CUELINK_API_URL,
                json={"url": product_url},
                headers={
                    "Authorization": f"Bearer {s.cuelink_api_key}",
                    "Content-Type":  "application/json",
                    "Accept":        "application/json",
                },
            )

        if r.status_code == 200:
            data = r.json()
            # CueLink returns {"shortUrl": "...", "affiliateUrl": "..."}
            short = (
                data.get("shortUrl")
                or data.get("affiliateUrl")
                or data.get("short_url")
            )
            if short and short.startswith("http"):
                return short

        log.warning(
            "cuelink_api_error",
            status=r.status_code,
            body=r.text[:200],
            url=product_url,
        )
    except Exception as e:
        log.error("cuelink_request_failed", error=str(e), url=product_url)

    # Fallback: return the raw product URL (no tracking, but still functional)
    return product_url


def build_cuelink_url_sync(product_url: str) -> str:
    """
    Synchronous wrapper around build_cuelink_url.
    Used by scrapers that cannot run async (e.g. during parse phase inside a
    synchronous Scrapling callback). Runs in the current event loop if available,
    otherwise spins up a new one.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already inside an async context — fall back to raw URL during parse;
            # the caller should await build_cuelink_url() properly instead.
            return product_url
        return loop.run_until_complete(build_cuelink_url(product_url))
    except RuntimeError:
        return asyncio.run(build_cuelink_url(product_url))


# ─── Per-Platform convenience wrappers (async) ───────────────────────────────
# These are thin async wrappers so scrapers can call a named function per
# platform without coupling directly to the CueLink plumbing.

async def build_flipkart_affiliate_url(product_url: str) -> str:
    """Return a CueLink affiliate URL for a Flipkart product page."""
    return await build_cuelink_url(product_url)


async def build_myntra_affiliate_url(product_url: str) -> str:
    """Return a CueLink affiliate URL for a Myntra product page."""
    return await build_cuelink_url(product_url)


async def build_meesho_affiliate_url(product_url: str) -> str:
    """Return a CueLink affiliate URL for a Meesho product page."""
    return await build_cuelink_url(product_url)


async def build_ajio_affiliate_url(product_url: str) -> str:
    """Return a CueLink affiliate URL for an Ajio product page."""
    return await build_cuelink_url(product_url)


async def build_snapdeal_affiliate_url(product_url: str) -> str:
    """Return a CueLink affiliate URL for a Snapdeal product page."""
    return await build_cuelink_url(product_url)


# ─── Dispatcher ───────────────────────────────────────────────────────────────

async def build_affiliate_url(
    platform: str,
    product_url: str,
    asin: str = "",
) -> str:
    """
    Dispatcher — given a platform name, build the correct affiliate URL.
    Used by the /go/{platform}/{listing_id} endpoint.

    Args:
        platform:    Platform name string (e.g. "flipkart", "amazon").
        product_url: Original scraped product URL.
        asin:        Amazon ASIN (required for platform == "amazon").

    Returns:
        Tracked affiliate URL string.
    """
    platform = platform.lower()

    if platform == "amazon":
        return build_amazon_affiliate_url(asin) if asin else product_url

    # All others go through CueLink
    return await build_cuelink_url(product_url)
