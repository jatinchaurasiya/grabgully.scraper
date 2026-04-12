"""
api/deals.py
─────────────
Deal feed endpoints consumed by the Android app.
All responses are cached — DB is never hammered on every app open.
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Query, Depends, HTTPException
from pydantic import BaseModel

from core.logger import get_logger
from services.db import get_db
from services.cache import get_cache, TTL_DEALS

router = APIRouter(tags=["deals"])
log    = get_logger("api.deals")


class DealOut(BaseModel):
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
    in_stock:       bool
    updated_at:     str


@router.get("", response_model=list[DealOut])
async def get_deals(
    category:    Optional[str] = Query(None, description="Filter by category"),
    platform:    Optional[str] = Query(None, description="Filter by platform"),
    min_discount: int          = Query(0,    ge=0, le=100),
    page:         int          = Query(1,    ge=1),
    limit:        int          = Query(20,   ge=1, le=100),
):
    """
    Main deal feed. Supports category/platform filtering, pagination.
    Android app LazyVerticalStaggeredGrid calls this with page= param.
    """
    cache_key = f"deals:{category}:{platform}:{min_discount}:{page}:{limit}"
    cache = get_cache()
    cached = await cache.get(cache_key)
    if cached:
        return cached

    db     = get_db()
    offset = (page - 1) * limit

    query = (
        db.table("platform_listings")
        .select("id, title, brand, image_url, platform, current_price, "
                "original_price, discount_pct, affiliate_url, category, "
                "in_stock, updated_at")
        .eq("in_stock", True)
        .gte("discount_pct", min_discount)
        .order("updated_at", desc=True)
        .range(offset, offset + limit - 1)
    )

    if category:
        query = query.eq("category", category)
    if platform:
        query = query.eq("platform", platform.lower())

    try:
        res  = query.execute()
        data = res.data or []
        await cache.set(cache_key, data, TTL_DEALS)
        return data
    except Exception as e:
        log.error("get_deals_failed", error=str(e))
        raise HTTPException(500, "Failed to fetch deals")


@router.get("/top", response_model=list[DealOut])
async def get_top_deals(limit: int = Query(10, ge=1, le=30)):
    """
    'Aaj Ke Dhamakedar Deals' — top deals by discount %.
    Used in the HomeScreen hero banner carousel.
    """
    cache = get_cache()
    cache_key = f"deals:top:{limit}"
    cached = await cache.get(cache_key)
    if cached:
        return cached

    db = get_db()
    try:
        res = (
            db.table("platform_listings")
            .select("id, title, brand, image_url, platform, current_price, "
                    "original_price, discount_pct, affiliate_url, category, "
                    "in_stock, updated_at")
            .eq("in_stock", True)
            .gte("discount_pct", 20)
            .order("discount_pct", desc=True)
            .limit(limit)
            .execute()
        )
        data = res.data or []
        await cache.set(cache_key, data, 300)   # 5 min — top deals refresh faster
        return data
    except Exception as e:
        log.error("get_top_deals_failed", error=str(e))
        raise HTTPException(500, "Failed to fetch top deals")


@router.get("/{deal_id}", response_model=DealOut)
async def get_deal(deal_id: str):
    """Fetch a single deal by ID. Used when opening a DealCard."""
    cache = get_cache()
    cache_key = f"deal:{deal_id}"
    cached = await cache.get(cache_key)
    if cached:
        return cached

    db = get_db()
    try:
        res = (
            db.table("platform_listings")
            .select("*")
            .eq("id", deal_id)
            .single()
            .execute()
        )
        if not res.data:
            raise HTTPException(404, "Deal not found")
        await cache.set(cache_key, res.data, TTL_DEALS)
        return res.data
    except HTTPException:
        raise
    except Exception as e:
        log.error("get_deal_failed", deal_id=deal_id, error=str(e))
        raise HTTPException(500, "Failed to fetch deal")
