"""
api/search.py
─────────────
Universal search — queries DB + Amazon PA-API + Flipkart simultaneously.
Results are merged, de-duplicated, and sorted by relevance.
Also handles URL paste: user pastes any product URL → we find it everywhere.
"""
from __future__ import annotations
import asyncio
import hashlib
import re
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from core.logger import get_logger
from services.db import get_db
from services.cache import get_cache, TTL_SEARCH
from integrations.amazon import get_amazon
from integrations.flipkart import get_flipkart

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
    source:         str    # "db" | "amazon" | "flipkart"


@router.get("", response_model=list[SearchResult])
async def search(
    q:        str          = Query(..., min_length=2, description="Search query"),
    platform: Optional[str] = Query(None, description="Filter by platform"),
    limit:    int          = Query(20, ge=1, le=50),
):
    """
    Universal search. Queries our scraped DB + live Amazon/Flipkart APIs.
    Results merged and sorted: highest discount first.
    """
    cache    = get_cache()
    cache_key = f"search:{hashlib.md5(f'{q}:{platform}:{limit}'.encode()).hexdigest()}"
    cached   = await cache.get(cache_key)
    if cached:
        return cached

    # Run DB + API searches concurrently
    tasks = [_search_db(q, platform, limit)]
    if not platform or platform == "amazon":
        tasks.append(_search_amazon(q, limit // 2))
    if not platform or platform == "flipkart":
        tasks.append(_search_flipkart(q, limit // 2))

    results_nested = await asyncio.gather(*tasks, return_exceptions=True)

    merged: list[dict] = []
    seen_titles: set[str] = set()

    for batch in results_nested:
        if isinstance(batch, Exception):
            log.warning("search_source_failed", error=str(batch))
            continue
        for item in (batch or []):
            # De-duplicate by normalised title
            key = re.sub(r"\W", "", item.get("title", "").lower())[:40]
            if key not in seen_titles:
                seen_titles.add(key)
                merged.append(item)

    # Sort: highest discount first
    merged.sort(key=lambda x: x.get("discount_pct", 0), reverse=True)
    result = merged[:limit]

    await cache.set(cache_key, result, TTL_SEARCH)
    return result


@router.get("/url", response_model=list[SearchResult])
async def search_by_url(url: str = Query(..., description="Product URL to find across platforms")):
    """
    URL Paste feature — paste any Amazon/Flipkart/Myntra product URL.
    We extract the product title/ASIN and find it on all platforms.
    """
    parsed  = urlparse(url)
    domain  = parsed.netloc.lower()
    product_name = ""
    asin         = ""

    # Extract product identifier from URL
    if "amazon" in domain:
        # Amazon: /dp/ASIN or /gp/product/ASIN
        match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
        if match:
            asin = match.group(1)
    elif "flipkart" in domain:
        # Flipkart: product name is in URL slug before /p/
        match = re.search(r"flipkart\.com/([^/]+)/p/", url)
        if match:
            product_name = match.group(1).replace("-", " ")
    elif "myntra" in domain:
        # Myntra: brand-product/pid
        parts = parsed.path.rstrip("/").split("/")
        if len(parts) >= 2:
            product_name = parts[-2].replace("-", " ")
    else:
        # Unknown platform — use path as query
        product_name = parsed.path.rstrip("/").split("/")[-1].replace("-", " ")

    query = product_name or asin
    if not query:
        raise HTTPException(400, "Could not extract product from URL")

    return await search(q=query, platform=None, limit=20)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _search_db(q: str, platform: Optional[str], limit: int) -> list[dict]:
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


async def _search_amazon(q: str, limit: int) -> list[dict]:
    try:
        products = await get_amazon().search_items(q, count=limit)
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
            "source":         "amazon",
        } for p in products]
    except Exception as e:
        log.warning("amazon_search_failed", q=q, error=str(e))
        return []


async def _search_flipkart(q: str, limit: int) -> list[dict]:
    try:
        products = await get_flipkart().search_products(q, count=limit)
        return [{
            "id":             p.external_id,
            "title":          p.title,
            "brand":          p.brand,
            "image_url":      p.image_url,
            "platform":       "flipkart",
            "current_price":  p.current_price,
            "original_price": p.original_price,
            "discount_pct":   p.computed_discount(),
            "affiliate_url":  p.affiliate_url,
            "category":       p.category,
            "source":         "flipkart",
        } for p in products]
    except Exception as e:
        log.warning("flipkart_search_failed", q=q, error=str(e))
        return []
