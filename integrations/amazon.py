"""
integrations/amazon.py
───────────────────────
Amazon Product Advertising API 5.0 wrapper.
PA-API gives real-time prices + product data — no scraping needed for Amazon.
Rate limit: 1 request/second per credentials pair.
We cache every response for 15 minutes to stay within limits.

Sign up: https://affiliate.amazon.in → Tools → Product Advertising API
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx

from core.config import get_settings
from core.exceptions import AffiliateAPIError
from core.logger import get_logger
from core.models import Platform, ScrapedProduct
from integrations.affiliate import build_amazon_affiliate_url
from services.cache import get_cache, TTL_DEALS

log = get_logger("amazon")

PAAPI_HOST   = "webservices.amazon.in"
PAAPI_REGION = "us-east-1"
PAAPI_PATH   = "/paapi5/searchitems"

# Amazon category → Browse Node IDs (India)
BROWSE_NODES = {
    "smartphones":  "1389401031",
    "laptops":      "1375424031",
    "earphones":    "1388921031",
    "headphones":   "1388921031",
    "smartwatches": "1350387031",
    "tablets":      "1375429031",
    "cameras":      "1389378031",
    "televisions":  "1389396031",
    "books":        "976389031",
    "kitchen":      "4430354031",
    "sports":       "3401328031",
}


class AmazonPAAPI:
    """
    Amazon PA-API 5.0 client using manual AWS Signature V4.
    Does NOT use the official SDK (adds 50MB+ of deps).
    """

    def __init__(self):
        s = get_settings()
        self.access_key   = s.amazon_access_key
        self.secret_key   = s.amazon_secret_key
        self.partner_tag  = s.amazon_partner_tag
        self.host         = s.amazon_host or PAAPI_HOST
        self.region       = s.amazon_region or PAAPI_REGION
        self._semaphore   = asyncio.Semaphore(1)   # Max 1 concurrent PA-API call
        self._last_call   = 0.0                    # Timestamp of last API call

    # ── Public API ────────────────────────────────────────────────────────────

    async def search_items(
        self,
        keywords: str,
        category: str = "All",
        count: int = 20,
    ) -> list[ScrapedProduct]:
        """Search Amazon for products by keyword. Returns list of ScrapedProduct."""
        if not self.access_key:
            log.warning("amazon_paapi_not_configured")
            return []

        cache = get_cache()
        cache_key = f"amz:search:{hashlib.md5(keywords.encode()).hexdigest()}"
        cached = await cache.get(cache_key)
        if cached:
            log.debug("amazon_cache_hit", keywords=keywords)
            return [ScrapedProduct(**p) for p in cached]

        payload = {
            "Keywords":     keywords,
            "Resources":    [
                "Images.Primary.Large",
                "ItemInfo.Title",
                "ItemInfo.ByLineInfo",
                "Offers.Listings.Price",
                "Offers.Listings.SavingBasis",
                "Offers.Listings.DeliveryInfo.IsFreeShippingEligible",
                "Offers.Summaries.LowestPrice",
                "BrowseNodeInfo.BrowseNodes",
            ],
            "SearchIndex":  category,
            "ItemCount":    min(count, 10),   # PA-API max is 10 per call
            "PartnerTag":   self.partner_tag,
            "PartnerType":  "Associates",
            "Marketplace":  "www.amazon.in",
        }

        try:
            data = await self._signed_request(PAAPI_PATH, payload)
            products = self._parse_search_response(data)
            if products:
                await cache.set(cache_key, [p.model_dump() for p in products], TTL_DEALS)
            return products
        except AffiliateAPIError:
            raise
        except Exception as e:
            log.error("amazon_search_failed", keywords=keywords, error=str(e))
            return []

    async def get_items(self, asins: list[str]) -> list[ScrapedProduct]:
        """Fetch specific items by ASIN. Used for price refresh on watchlist items."""
        if not asins or not self.access_key:
            return []

        payload = {
            "ItemIds":    asins[:10],   # max 10 per call
            "Resources":  [
                "Images.Primary.Large",
                "ItemInfo.Title",
                "ItemInfo.ByLineInfo",
                "Offers.Listings.Price",
                "Offers.Listings.SavingBasis",
            ],
            "PartnerTag":  self.partner_tag,
            "PartnerType": "Associates",
            "Marketplace": "www.amazon.in",
        }

        try:
            data = await self._signed_request("/paapi5/getitems", payload)
            return self._parse_items_response(data)
        except Exception as e:
            log.error("amazon_getitems_failed", asins=asins, error=str(e))
            return []

    # ── Parsers ───────────────────────────────────────────────────────────────

    def _parse_search_response(self, data: dict) -> list[ScrapedProduct]:
        items = (
            data.get("SearchResult", {})
                .get("Items", [])
        )
        return [p for p in (self._parse_item(i) for i in items) if p]

    def _parse_items_response(self, data: dict) -> list[ScrapedProduct]:
        items = data.get("ItemsResult", {}).get("Items", [])
        return [p for p in (self._parse_item(i) for i in items) if p]

    def _parse_item(self, item: dict) -> Optional[ScrapedProduct]:
        try:
            asin      = item.get("ASIN", "")
            if not asin:
                return None

            # Title
            title = (
                item.get("ItemInfo", {})
                    .get("Title", {})
                    .get("DisplayValue", "")
            )
            if not title:
                return None

            # Brand
            brand = (
                item.get("ItemInfo", {})
                    .get("ByLineInfo", {})
                    .get("Brand", {})
                    .get("DisplayValue", "")
            )

            # Image
            image_url = (
                item.get("Images", {})
                    .get("Primary", {})
                    .get("Large", {})
                    .get("URL", "")
            )

            # Price
            listings = (
                item.get("Offers", {})
                    .get("Listings", [])
            )
            current_price  = 0.0
            original_price = 0.0

            if listings:
                listing = listings[0]
                current_price = float(
                    listing.get("Price", {})
                           .get("Amount", 0) or 0
                )
                original_price = float(
                    listing.get("SavingBasis", {})
                           .get("Amount", 0) or current_price
                )

            if current_price <= 0:
                return None

            discount = 0
            if original_price > current_price:
                discount = int((1 - current_price / original_price) * 100)

            return ScrapedProduct(
                external_id    = asin,
                platform       = Platform.AMAZON,
                title          = title,
                brand          = brand,
                image_url      = image_url,
                current_price  = current_price,
                original_price = original_price,
                discount_pct   = discount,
                affiliate_url  = build_amazon_affiliate_url(asin),
                category       = "",
            )
        except Exception as e:
            log.debug("amazon_item_parse_error", error=str(e))
            return None

    # ── AWS Signature V4 ──────────────────────────────────────────────────────

    async def _signed_request(self, path: str, payload: dict) -> dict:
        """
        Make an AWS Signature V4 signed request to PA-API.
        Enforces 1 req/sec rate limit via semaphore + sleep.
        """
        async with self._semaphore:
            # Polite 1 req/sec rate limit
            elapsed = time.monotonic() - self._last_call
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)

            headers = self._build_headers(path, payload)
            url = f"https://{self.host}{path}"

            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                )
                self._last_call = time.monotonic()

                if r.status_code == 429:
                    raise AffiliateAPIError("Amazon PA-API rate limited (429)")
                if r.status_code == 403:
                    raise AffiliateAPIError("Amazon PA-API auth failed — check credentials")
                if r.status_code != 200:
                    raise AffiliateAPIError(f"Amazon PA-API error {r.status_code}: {r.text[:200]}")

                return r.json()

    def _build_headers(self, path: str, payload: dict) -> dict:
        """Build AWS Signature V4 signed headers."""
        body          = json.dumps(payload, separators=(",", ":"))
        body_hash     = hashlib.sha256(body.encode()).hexdigest()
        now           = datetime.now(timezone.utc)
        amz_date      = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp    = now.strftime("%Y%m%d")
        service       = "ProductAdvertisingAPI"
        target        = "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems"
        if "getitems" in path:
            target = "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems"

        canonical_headers = (
            f"content-encoding:amz-1.0\n"
            f"content-type:application/json; charset=utf-8\n"
            f"host:{self.host}\n"
            f"x-amz-date:{amz_date}\n"
            f"x-amz-target:{target}\n"
        )
        signed_headers = "content-encoding;content-type;host;x-amz-date;x-amz-target"

        canonical_request = "\n".join([
            "POST", path, "",
            canonical_headers, signed_headers, body_hash
        ])

        credential_scope = f"{date_stamp}/{self.region}/{service}/aws4_request"
        string_to_sign   = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest()
        ])

        def _sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        signing_key = _sign(
            _sign(
                _sign(
                    _sign(f"AWS4{self.secret_key}".encode(), date_stamp),
                    self.region
                ),
                service
            ),
            "aws4_request"
        )
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth_header = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        return {
            "Content-Encoding": "amz-1.0",
            "Content-Type":     "application/json; charset=utf-8",
            "Host":             self.host,
            "X-Amz-Date":       amz_date,
            "X-Amz-Target":     target,
            "Authorization":    auth_header,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_amazon: Optional[AmazonPAAPI] = None

def get_amazon() -> AmazonPAAPI:
    global _amazon
    if _amazon is None:
        _amazon = AmazonPAAPI()
    return _amazon
