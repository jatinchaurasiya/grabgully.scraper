"""
api/compare.py
───────────────
Price comparison endpoints — the heart of the CompareScreen in the Android app.
Given a product ID, returns prices from all platforms + 90-day history.
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from core.logger import get_logger
from services.db import get_db
from services.cache import get_cache, TTL_COMPARE, TTL_PRICE_HISTORY

router = APIRouter(tags=["compare"])
log    = get_logger("api.compare")


class PlatformPrice(BaseModel):
    listing_id:    str
    platform:      str
    current_price: float
    original_price: float
    discount_pct:  int
    affiliate_url: str
    in_stock:      bool
    updated_at:    str
    is_cheapest:   bool = False


class CompareResponse(BaseModel):
    product_title:  str
    image_url:      str
    category:       str
    listings:       list[PlatformPrice]
    cheapest:       Optional[PlatformPrice]
    price_drop_24h: Optional[float]    # Rs amount dropped in last 24h (None if no change)


class PriceHistoryPoint(BaseModel):
    price:      float
    scraped_at: str


@router.get("/{listing_id}", response_model=CompareResponse)
async def compare_prices(listing_id: str):
    """
    Main compare endpoint. Given any listing_id, fetches:
    - All platform prices for the same product
    - Marks the cheapest
    - Includes 24h price drop info
    """
    cache     = get_cache()
    cache_key = f"compare:{listing_id}"
    cached    = await cache.get(cache_key)
    if cached:
        return cached

    db = get_db()
    try:
        # Get the base listing
        base_res = (
            db.table("platform_listings")
            .select("id, title, image_url, category, brand")
            .eq("id", listing_id)
            .single()
            .execute()
        )
        if not base_res.data:
            raise HTTPException(404, "Product not found")

        base   = base_res.data
        title  = base.get("title", "")
        # Normalised title for cross-platform matching (first 40 chars, no special chars)
        import re
        normalised = re.sub(r"[^\w\s]", "", title).lower().strip()[:40]

        # Find same/similar products on other platforms
        similar_res = (
            db.table("platform_listings")
            .select("id, platform, current_price, original_price, "
                    "discount_pct, affiliate_url, in_stock, updated_at")
            .ilike("title", f"%{normalised[:20]}%")
            .eq("in_stock", True)
            .limit(10)
            .execute()
        )

        listings_data = similar_res.data or []
        if not listings_data:
            # Fallback: just return this listing alone
            own_res = (
                db.table("platform_listings")
                .select("id, platform, current_price, original_price, "
                        "discount_pct, affiliate_url, in_stock, updated_at")
                .eq("id", listing_id)
                .execute()
            )
            listings_data = own_res.data or []

        # Build response
        listings = [
            PlatformPrice(
                listing_id    = r["id"],
                platform      = r["platform"],
                current_price = r["current_price"],
                original_price = r.get("original_price", r["current_price"]),
                discount_pct  = r.get("discount_pct", 0),
                affiliate_url = r["affiliate_url"],
                in_stock      = r.get("in_stock", True),
                updated_at    = str(r.get("updated_at", "")),
            )
            for r in listings_data
        ]

        # Mark cheapest
        cheapest = None
        if listings:
            cheapest = min(listings, key=lambda x: x.current_price)
            cheapest.is_cheapest = True

        # 24h price drop
        price_drop = await _get_24h_drop(listing_id)

        result = CompareResponse(
            product_title  = title,
            image_url      = base.get("image_url", ""),
            category       = base.get("category", ""),
            listings       = listings,
            cheapest       = cheapest,
            price_drop_24h = price_drop,
        )
        await cache.set(cache_key, result.model_dump(), TTL_COMPARE)
        return result

    except HTTPException:
        raise
    except Exception as e:
        log.error("compare_failed", listing_id=listing_id, error=str(e))
        raise HTTPException(500, "Failed to compare prices")


@router.get("/{listing_id}/history", response_model=list[PriceHistoryPoint])
async def price_history(
    listing_id: str,
    days:       int = Query(90, ge=7, le=365),
):
    """
    90-day price history for a listing.
    Feeds the Vico line chart in CompareScreen.
    """
    cache     = get_cache()
    cache_key = f"history:{listing_id}:{days}"
    cached    = await cache.get(cache_key)
    if cached:
        return cached

    db = get_db()
    try:
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        res = (
            db.table("price_history")
            .select("price, scraped_at")
            .eq("listing_id", listing_id)
            .gte("scraped_at", since)
            .order("scraped_at", desc=False)
            .execute()
        )
        data = res.data or []
        await cache.set(cache_key, data, TTL_PRICE_HISTORY)
        return data
    except Exception as e:
        log.error("history_failed", listing_id=listing_id, error=str(e))
        raise HTTPException(500, "Failed to fetch price history")


# ── Internal ──────────────────────────────────────────────────────────────────

async def _get_24h_drop(listing_id: str) -> Optional[float]:
    """Compare current price with price from ~24h ago. Returns drop amount or None."""
    db = get_db()
    try:
        from datetime import datetime, timedelta, timezone
        yesterday = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        ago_24h   = (datetime.now(timezone.utc) - timedelta(hours=23)).isoformat()

        # Price ~24h ago
        old_res = (
            db.table("price_history")
            .select("price")
            .eq("listing_id", listing_id)
            .gte("scraped_at", yesterday)
            .lte("scraped_at", ago_24h)
            .order("scraped_at", desc=False)
            .limit(1)
            .execute()
        )
        # Current price
        now_res = (
            db.table("price_history")
            .select("price")
            .eq("listing_id", listing_id)
            .order("scraped_at", desc=True)
            .limit(1)
            .execute()
        )

        old_data = old_res.data
        now_data = now_res.data

        if not old_data or not now_data:
            return None

        old_price = float(old_data[0]["price"])
        new_price = float(now_data[0]["price"])
        drop = old_price - new_price

        return round(drop, 2) if drop > 0 else None
    except Exception:
        return None
