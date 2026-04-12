"""
integrations/amazon.py
───────────────────────
Amazon Creator API (Content Creator API) wrapper.
This replaces the PA-API 5.0. Creator API gives access to product search
and deal data via an OAuth2 bearer token (no AWS Signature V4 required).

Sign up: https://affiliate.amazon.in → Tools → Creator API (Content Creator)
Docs:    https://webservices.amazon.com/paapi5/documentation (Creator endpoints)

Rate limits: 1 request/second per credentials pair.
All responses are cached for 15 minutes.
"""
from __future__ import annotations
import asyncio
import hashlib
from typing import Optional

import httpx

from core.config import get_settings
from core.exceptions import AffiliateAPIError
from core.logger import get_logger
from core.models import Platform, ScrapedProduct
from integrations.affiliate import build_amazon_affiliate_url
from services.cache import get_cache, TTL_DEALS

log = get_logger("amazon")

# Amazon Creator API base (India marketplace)
CREATOR_API_BASE    = "https://affiliate-program.amazon.in/creatorAPI"
CREATOR_TOKEN_URL   = "https://api.amazon.in/auth/o2/token"

# Product-search endpoint (Creator API v1)
SEARCH_PATH         = "/products/search"
DEALS_PATH          = "/products/deals"


class AmazonCreatorAPI:
    """
    Amazon Creator API client using OAuth2 Bearer Token (LWA — Login with Amazon).
    Much simpler than PA-API 5.0 — no AWS Signature V4 needed.

    Prerequisites:
    - AMAZON_CLIENT_ID      → From Amazon Associates / Creator Program dashboard
    - AMAZON_CLIENT_SECRET  → Same dashboard
    - AMAZON_PARTNER_TAG    → Your Associates tracking ID (e.g. grabgully-21)
    """

    def __init__(self):
        s = self._s = get_settings()
        self.client_id      = s.amazon_client_id
        self.client_secret  = s.amazon_client_secret
        self.partner_tag    = s.amazon_partner_tag
        self._access_token: Optional[str]  = None
        self._token_expiry: float          = 0.0
        self._semaphore = asyncio.Semaphore(1)    # 1 concurrent call max
        self._last_call = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    async def search_products(
        self,
        keywords: str,
        category: str = "",
        count: int = 20,
    ) -> list[ScrapedProduct]:
        """
        Search Amazon India for products by keyword via Creator API.
        Returns list of ScrapedProduct (empty list if API not configured).
        """
        if not self.client_id or not self.client_secret:
            log.warning("amazon_creator_api_not_configured")
            return []

        cache = get_cache()
        cache_key = f"amz:creator:search:{hashlib.md5(keywords.encode()).hexdigest()}"
        cached = await cache.get(cache_key)
        if cached:
            log.debug("amazon_cache_hit", keywords=keywords)
            return [ScrapedProduct(**p) for p in cached]

        params = {
            "keywords":      keywords,
            "tag":           self.partner_tag,
            "maxResults":    min(count, 20),
        }
        if category:
            params["category"] = category

        try:
            data     = await self._request("GET", SEARCH_PATH, params=params)
            products = self._parse_search_response(data)
            if products:
                await cache.set(cache_key, [p.model_dump() for p in products], TTL_DEALS)
            return products
        except AffiliateAPIError:
            raise
        except Exception as e:
            log.error("amazon_search_failed", keywords=keywords, error=str(e))
            return []

    async def get_deals(self, count: int = 20) -> list[ScrapedProduct]:
        """
        Fetch current Amazon deals / lightning deals via the Creator API.
        These are curated deals shown on amazon.in/deals.
        """
        if not self.client_id or not self.client_secret:
            return []

        cache = get_cache()
        cache_key = "amz:creator:deals"
        cached = await cache.get(cache_key)
        if cached:
            return [ScrapedProduct(**p) for p in cached]

        params = {"tag": self.partner_tag, "maxResults": min(count, 20)}
        try:
            data     = await self._request("GET", DEALS_PATH, params=params)
            products = self._parse_search_response(data)
            if products:
                await cache.set(cache_key, [p.model_dump() for p in products], TTL_DEALS)
            return products
        except Exception as e:
            log.error("amazon_deals_failed", error=str(e))
            return []

    # ── Response Parsers ──────────────────────────────────────────────────────

    def _parse_search_response(self, data: dict) -> list[ScrapedProduct]:
        """Parse Creator API search/deals response into ScrapedProduct list."""
        items = (
            data.get("products", [])
            or data.get("items", [])
            or data.get("results", [])
        )
        return [p for p in (self._parse_item(i) for i in items) if p]

    def _parse_item(self, item: dict) -> Optional[ScrapedProduct]:
        try:
            asin  = item.get("asin", "") or item.get("id", "")
            if not asin:
                return None

            title = item.get("title", "") or item.get("name", "")
            if not title:
                return None

            brand     = item.get("brand", "") or item.get("brandName", "")
            image_url = (
                item.get("imageUrl", "")
                or item.get("image", {}).get("large", "")
                or item.get("image", {}).get("medium", "")
            )

            # Creator API may return price as float or nested dict
            price_info     = item.get("price", {})
            if isinstance(price_info, dict):
                current_price  = float(price_info.get("amount", 0) or 0)
                original_price = float((
                    price_info.get("mrp") or price_info.get("originalAmount") or current_price
                ))
            else:
                current_price  = float(price_info or 0)
                original_price = float(item.get("mrp") or current_price)

            if current_price <= 0:
                return None

            discount = 0
            if original_price > current_price:
                discount = int((1 - current_price / original_price) * 100)

            return ScrapedProduct(
                external_id    = asin,
                platform       = Platform.AMAZON,
                title          = title.strip(),
                brand          = brand,
                image_url      = image_url,
                current_price  = current_price,
                original_price = original_price,
                discount_pct   = discount,
                affiliate_url  = build_amazon_affiliate_url(asin),
                category       = item.get("category", ""),
                rating         = float(item.get("rating", 0) or 0),
                rating_count   = int(item.get("ratingCount", 0) or 0),
            )
        except Exception as e:
            log.debug("amazon_item_parse_error", error=str(e))
            return None

    # ── OAuth2 Token (LWA) ────────────────────────────────────────────────────

    async def _get_token(self) -> str:
        """
        Fetch or refresh the LWA (Login with Amazon) OAuth2 access token.
        Tokens are cached until expiry with a 60-second safety margin.
        """
        import time
        if self._access_token and time.monotonic() < self._token_expiry:
            return self._access_token

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                CREATOR_TOKEN_URL,
                data={
                    "grant_type":    "client_credentials",
                    "client_id":     self.client_id,
                    "client_secret": self.client_secret,
                    "scope":         "advertising::creator:read",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if r.status_code == 401:
            raise AffiliateAPIError("Amazon Creator API: invalid credentials (401)")
        if r.status_code != 200:
            raise AffiliateAPIError(
                f"Amazon Creator API token error {r.status_code}: {r.text[:200]}"
            )

        payload             = r.json()
        self._access_token  = payload["access_token"]
        self._token_expiry  = time.monotonic() + payload.get("expires_in", 3600) - 60
        log.debug("amazon_token_refreshed")
        return self._access_token

    # ── HTTP Helper  ──────────────────────────────────────────────────────────

    async def _request(self, method: str, path: str, params: dict | None = None) -> dict:
        """Rate-limited HTTP call to Creator API with Bearer auth."""
        import time
        async with self._semaphore:
            elapsed = time.monotonic() - self._last_call
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)

            token = await self._get_token()
            url   = f"{CREATOR_API_BASE}{path}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept":        "application/json",
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.request(
                    method, url, params=params, headers=headers
                )
            self._last_call = time.monotonic()

            if r.status_code == 429:
                raise AffiliateAPIError("Amazon Creator API: rate limited (429)")
            if r.status_code == 401:
                # Token may have expired mid-flight — clear and let caller retry
                self._access_token = None
                raise AffiliateAPIError("Amazon Creator API: unauthorized (401)")
            if r.status_code != 200:
                raise AffiliateAPIError(
                    f"Amazon Creator API error {r.status_code}: {r.text[:200]}"
                )
            return r.json()


# ── Singleton ─────────────────────────────────────────────────────────────────
_amazon: Optional[AmazonCreatorAPI] = None


def get_amazon() -> AmazonCreatorAPI:
    """Return the module-level singleton AmazonCreatorAPI instance."""
    global _amazon
    if _amazon is None:
        _amazon = AmazonCreatorAPI()
    return _amazon
