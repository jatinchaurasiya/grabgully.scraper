"""
api/search.py
─────────────
Universal search — queries the scraped DB + Amazon Creator API.
Results are merged, de-duplicated, sorted by discount %.

Flipkart data comes from the scraped DB (scrapers/flipkart.py runs on
the scheduler). There is no live Flipkart API call here anymore — the
Flipkart Affiliate API has been removed.

URL paste: user pastes any product URL → we extract a search term and
find similar products across all platforms.
"""
from __future__ import annotations
import asyncio
import hashlib
import re
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Query, HTTPException, Request
from pydantic import BaseModel

from core.logger import get_logger
from core.limiter import limiter
from services.db import get_db
from services.cache import get_cache, TTL_SEARCH
from integrations.amazon import get_amazon

router = APIRouter(tags=["search"])
log    = get_logger("api.search")


class SearchResult(BaseModel):
    id:             str
    title:          str
    brand:          str
    image_url:      str
    platform:       str
    current_price:  float
    original_price: float
    discount_pct:   int
    affiliate_url:  str
    category:       str
    source:         str    # "db" | "amazon_creator"


@router.get("", response_model=list[SearchResult])
@limiter.limit("30/minute")
async def search(
    request:  Request,
    q:        str           = Query(..., min_length=2, description="Search query"),
    platform: Optional[str] = Query(None, description="Filter by platform"),
    limit:    int           = Query(20, ge=1, le=50),
):
    """
    Universal search.
    Queries scraped DB (all platforms) + Amazon Creator API (live).
    Results merged and sorted: highest discount first.
    """
    cache     = get_cache()
    cache_key = f"search:{hashlib.md5(f'{q}:{platform}:{limit}'.encode()).hexdigest()}"
    cached    = await cache.get(cache_key)
    if cached:
        return cached

    # Run DB + Amazon concurrently
    tasks = [_search_db(q, platform, limit)]
    if not platform or platform == "amazon":
        tasks.append(_search_amazon_creator(q, limit // 2))

    results_nested = await asyncio.gather(*tasks, return_exceptions=True)

    merged: list[dict] = []
    seen: set[str] = set()

    for batch in results_nested:
        if isinstance(batch, Exception):
            log.warning("search_source_failed", error=str(batch))
            continue
        for item in (batch or []):
            key = re.sub(r"\W", "", item.get("title", "").lower())[:40]
            if key not in seen:
                seen.add(key)
                merged.append(item)

    merged.sort(key=lambda x: x.get("discount_pct", 0), reverse=True)
    result = merged[:limit]

    await cache.set(cache_key, result, TTL_SEARCH)
    return result


@router.get("/url", response_model=list[SearchResult])
async def search_by_url(
    request: Request,
    url: str = Query(..., description="Product URL to find across platforms")
):
    """
    URL Paste — paste any product URL to find it across all platforms.
    Supports Amazon, Flipkart, Myntra, Meesho, Ajio, Snapdeal URLs.
    """
    parsed       = urlparse(url)
    domain       = parsed.netloc.lower()
    product_name = ""
    asin         = ""

    if "amazon" in domain:
        m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
        if m:
            asin = m.group(1)
    elif "flipkart" in domain:
        m = re.search(r"flipkart\.com/([^/]+)/p/", url)
        if m:
            product_name = m.group(1).replace("-", " ")
    elif "myntra" in domain:
        parts = parsed.path.rstrip("/").split("/")
        if len(parts) >= 2:
            product_name = parts[-2].replace("-", " ")
    elif "meesho" in domain:
        parts = parsed.path.rstrip("/").split("/")
        product_name = parts[-2].replace("-", " ") if len(parts) >= 2 else parts[-1]
    elif "ajio" in domain:
        parts = parsed.path.rstrip("/").split("/")
        product_name = parts[-1].split("?")[0].replace("-", " ")
    else:
        product_name = parsed.path.rstrip("/").split("/")[-1].replace("-", " ")

    query = product_name or asin
    if not query:
        raise HTTPException(400, "Could not extract a product identifier from this URL")

    return await search(request=request, q=query, platform=None, limit=20)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _search_db(q: str, platform: Optional[str], limit: int) -> list[dict]:
    """Search the scraped product database using ILIKE for fuzzy title match."""
    db = get_db()
    try:
        query = (
            db.table("platform_listings")
            .select("id, title, brand, image_url, platform, current_price, "
                    "original_price, discount_pct, affiliate_url, category")
            .ilike("title", f"%{q}%")
            .eq("in_stock", True)
            .order("discount_pct", desc=True)
            .limit(limit)
        )
        if platform:
            query = query.eq("platform", platform.lower())
        res = query.execute()
        return [{**r, "source": "db"} for r in (res.data or [])]
    except Exception as e:
        log.warning("db_search_failed", q=q, error=str(e))
        return []


async def _search_amazon_creator(q: str, limit: int) -> list[dict]:
    """Live Amazon search via Creator API. Gracefully returns [] if not configured."""
    try:
        products = await get_amazon().search_products(q, count=limit)
        return [{
            "id":             p.external_id,
            "title":          p.title,
            "brand":          p.brand,
            "image_url":      p.image_url,
            "platform":       "amazon",
            "current_price":  p.current_price,
            "original_price": p.original_price,
            "discount_pct":   p.computed_discount(),
            "affiliate_url":  p.affiliate_url,
            "category":       p.category,
            "source":         "amazon_creator",
        } for p in products]
    except Exception as e:
        log.warning("amazon_creator_search_failed", q=q, error=str(e))
        return []
