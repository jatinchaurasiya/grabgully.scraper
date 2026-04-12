"""
integrations/flipkart.py
─────────────────────────
Flipkart Affiliate API wrapper.
Gives real-time product data + prices — no scraping needed.

Sign up: https://affiliate.flipkart.com
API docs: https://affiliate.flipkart.com/api-docs
"""
from __future__ import annotations
import hashlib
from typing import Optional

import httpx

from core.config import get_settings
from core.exceptions import AffiliateAPIError
from core.logger import get_logger
from core.models import Platform, ScrapedProduct
from integrations.affiliate import build_flipkart_affiliate_url
from services.cache import get_cache, TTL_DEALS

log = get_logger("flipkart")

FK_API_BASE = "https://affiliate-api.flipkart.io/affiliate"


class FlipkartAffiliateAPI:

    def __init__(self):
        s = get_settings()
        self.affiliate_id    = s.flipkart_affiliate_id
        self.affiliate_token = s.flipkart_affiliate_token

    @property
    def _headers(self) -> dict:
        return {
            "Fk-Affiliate-Id":    self.affiliate_id,
            "Fk-Affiliate-Token": self.affiliate_token,
            "Accept":             "application/json",
        }

    # ── Public API ────────────────────────────────────────────────────────────

    async def search_products(
        self,
        query: str,
        category: str = "",
        count: int = 20,
    ) -> list[ScrapedProduct]:
        """Search Flipkart products. Returns up to `count` products."""
        if not self.affiliate_token:
            log.warning("flipkart_not_configured")
            return []

        cache = get_cache()
        cache_key = f"fk:search:{hashlib.md5(query.encode()).hexdigest()}"
        cached = await cache.get(cache_key)
        if cached:
            return [ScrapedProduct(**p) for p in cached]

        try:
            url    = f"{FK_API_BASE}/search/json"
            params = {"query": query, "resultCount": min(count, 20)}

            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, params=params, headers=self._headers)

            if r.status_code == 401:
                raise AffiliateAPIError("Flipkart API: invalid token (401)")
            if r.status_code == 429:
                raise AffiliateAPIError("Flipkart API: rate limited (429)")
            if r.status_code != 200:
                raise AffiliateAPIError(f"Flipkart API error {r.status_code}")

            products = self._parse_search(r.json(), category)
            if products:
                await cache.set(cache_key, [p.model_dump() for p in products], TTL_DEALS)
            return products

        except AffiliateAPIError:
            raise
        except Exception as e:
            log.error("flipkart_search_failed", query=query, error=str(e))
            return []

    async def get_category_feed(self, category_id: str) -> list[ScrapedProduct]:
        """
        Fetch a pre-built Flipkart category feed.
        Faster than search — returns curated top products.
        """
        if not self.affiliate_token:
            return []

        cache = get_cache()
        cache_key = f"fk:feed:{category_id}"
        cached = await cache.get(cache_key)
        if cached:
            return [ScrapedProduct(**p) for p in cached]

        url = f"{FK_API_BASE}/feeds/{category_id}/json"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, headers=self._headers)
            if r.status_code != 200:
                log.warning("flipkart_feed_failed",
                            category=category_id, status=r.status_code)
                return []
            products = self._parse_feed(r.json())
            if products:
                await cache.set(cache_key, [p.model_dump() for p in products], TTL_DEALS)
            return products
        except Exception as e:
            log.error("flipkart_feed_error", category=category_id, error=str(e))
            return []

    # ── Parsers ───────────────────────────────────────────────────────────────

    def _parse_search(self, data: dict, category: str) -> list[ScrapedProduct]:
        results = data.get("products", data.get("response", {}).get("products", []))
        return [p for p in (self._parse_product(r, category) for r in results) if p]

    def _parse_feed(self, data: dict) -> list[ScrapedProduct]:
        products = data.get("products", [])
        return [p for p in (self._parse_product(r, "") for r in products) if p]

    def _parse_product(self, item: dict, category: str) -> Optional[ScrapedProduct]:
        try:
            # Flipkart API returns productBaseInfo or product_info
            base  = item.get("productBaseInfo", item)
            info  = base.get("productInfo", base)
            attrs = info.get("productAttributes", info)

            product_id  = base.get("productId", "") or info.get("id", "")
            title       = attrs.get("title", "") or info.get("title", "")
            if not title or not product_id:
                return None

            # Price — Flipkart returns as string with "Rs." prefix
            price_str   = (
                attrs.get("discountedPrice")
                or attrs.get("sellingPrice")
                or info.get("sellingPrice", "0")
            )
            orig_str    = attrs.get("maximumRetailPrice", price_str)

            def _to_float(v) -> float:
                if isinstance(v, (int, float)):
                    return float(v)
                s = str(v).replace(",", "").replace("Rs.", "").replace("₹", "").strip()
                try:
                    return float(s)
                except ValueError:
                    return 0.0

            current_price  = _to_float(price_str)
            original_price = _to_float(orig_str)
            if current_price <= 0:
                return None

            # Image
            images    = info.get("imageUrls", {})
            image_url = (
                images.get("400x400", "")
                or images.get("200x200", "")
                or next(iter(images.values()), "")
            )

            # Flipkart URL
            product_url = base.get("productUrl", "") or info.get("productUrl", "")

            discount = 0
            if original_price > current_price:
                discount = int((1 - current_price / original_price) * 100)

            return ScrapedProduct(
                external_id    = str(product_id),
                platform       = Platform.FLIPKART,
                title          = title.strip(),
                brand          = attrs.get("brand", ""),
                image_url      = image_url,
                current_price  = current_price,
                original_price = original_price,
                discount_pct   = discount,
                affiliate_url  = build_flipkart_affiliate_url(product_url),
                category       = category,
                rating         = float(attrs.get("productRating", 0) or 0),
            )
        except Exception as e:
            log.debug("flipkart_parse_error", error=str(e))
            return None


# ── Singleton ─────────────────────────────────────────────────────────────────
_flipkart: Optional[FlipkartAffiliateAPI] = None

def get_flipkart() -> FlipkartAffiliateAPI:
    global _flipkart
    if _flipkart is None:
        _flipkart = FlipkartAffiliateAPI()
    return _flipkart
