"""
services/db.py
──────────────
All Supabase interactions in one place.
Repositories pattern — no raw Supabase calls anywhere else.
"""
from __future__ import annotations
from functools import lru_cache
from datetime import datetime, timezone
from typing import Optional
from supabase import create_client, Client
from core.config import get_settings
from core.exceptions import DatabaseError
from core.logger import get_logger
from core.models import ScrapedProduct, PriceAlert

log = get_logger("db")


@lru_cache
def get_db() -> Client:
    """Cached Supabase client — one connection reused across the app."""
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_key)


# ── Products & Listings ───────────────────────────────────────────────────────

async def upsert_listing(product: ScrapedProduct) -> Optional[str]:
    """
    Upsert a platform_listing row.
    Returns the listing id on success, None on failure.
    Conflict key: (platform, external_id) — update price on re-scrape.
    """
    db = get_db()
    row = {
        "platform":       product.platform.value,
        "external_id":    product.external_id,
        "title":          product.title,
        "brand":          product.brand,
        "image_url":      product.image_url,
        "current_price":  product.current_price,
        "original_price": product.original_price or product.current_price,
        "discount_pct":   product.computed_discount(),
        "affiliate_url":  product.affiliate_url,
        "category":       product.category,
        "in_stock":       product.in_stock,
        "rating":         product.rating,
        "rating_count":   product.rating_count,
        "updated_at":     datetime.now(timezone.utc).isoformat(),
    }
    try:
        res = (
            db.table("platform_listings")
            .upsert(row, on_conflict="platform,external_id")
            .execute()
        )
        if res.data:
            listing_id = res.data[0].get("id")
            log.debug("upserted_listing", platform=product.platform.value,
                      id=product.external_id, price=product.current_price)
            return listing_id
        return None
    except Exception as e:
        log.error("upsert_listing_failed", platform=product.platform.value,
                  external_id=product.external_id, error=str(e))
        raise DatabaseError(f"upsert_listing failed: {e}") from e


async def upsert_listings_bulk(products: list[ScrapedProduct]) -> int:
    """Bulk upsert — batches of 100 rows. Returns count of successful upserts."""
    if not products:
        return 0
    db = get_db()
    batch_size = 100
    success_count = 0
    rows = []
    for p in products:
        rows.append({
            "platform":       p.platform.value,
            "external_id":    p.external_id,
            "title":          p.title,
            "brand":          p.brand,
            "image_url":      p.image_url,
            "current_price":  p.current_price,
            "original_price": p.original_price or p.current_price,
            "discount_pct":   p.computed_discount(),
            "affiliate_url":  p.affiliate_url,
            "category":       p.category,
            "in_stock":       p.in_stock,
            "rating":         p.rating,
            "rating_count":   p.rating_count,
            "updated_at":     datetime.now(timezone.utc).isoformat(),
        })
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        try:
            res = (
                db.table("platform_listings")
                .upsert(batch, on_conflict="platform,external_id")
                .execute()
            )
            success_count += len(res.data or [])
        except Exception as e:
            log.error("bulk_upsert_failed", batch_start=i, error=str(e))
    return success_count


async def record_price_history(listing_id: str, price: float) -> None:
    """Append a price point to the price_history table."""
    db = get_db()
    try:
        db.table("price_history").insert({
            "listing_id": listing_id,
            "price":      price,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        log.warning("price_history_insert_failed", listing_id=listing_id, error=str(e))


# ── Watchlist / Alerts ────────────────────────────────────────────────────────

async def get_pending_alerts() -> list[dict]:
    """
    Returns all watchlist rows where:
    - target_price is set
    - current_price <= target_price
    - is_notified is False
    """
    db = get_db()
    try:
        res = (
            db.table("watchlist")
            .select(
                "id, user_id, target_price, "
                "platform_listings(id, title, platform, current_price, affiliate_url), "
                "users(fcm_token)"
            )
            .eq("is_notified", False)
            .not_.is_("target_price", "null")
            .execute()
        )
        alerts = []
        for row in res.data or []:
            listing = row.get("platform_listings") or {}
            user    = row.get("users") or {}
            current = listing.get("current_price", 999999)
            target  = row.get("target_price", 0)
            if current <= target:
                alerts.append({
                    "watchlist_id":  row["id"],
                    "user_id":       row["user_id"],
                    "listing_id":    listing.get("id"),
                    "product_title": listing.get("title", ""),
                    "platform":      listing.get("platform", ""),
                    "target_price":  target,
                    "current_price": current,
                    "affiliate_url": listing.get("affiliate_url", ""),
                    "fcm_token":     user.get("fcm_token"),
                })
        return alerts
    except Exception as e:
        log.error("get_pending_alerts_failed", error=str(e))
        return []


async def mark_alert_notified(watchlist_id: str) -> None:
    db = get_db()
    try:
        db.table("watchlist") \
          .update({"is_notified": True, "notified_at": datetime.now(timezone.utc).isoformat()}) \
          .eq("id", watchlist_id) \
          .execute()
    except Exception as e:
        log.warning("mark_alert_failed", watchlist_id=watchlist_id, error=str(e))


# ── Affiliate Click Logging ───────────────────────────────────────────────────

async def log_affiliate_click(
    user_id: Optional[str],
    listing_id: str,
    platform: str,
    ip_hash: str,
) -> None:
    """Audit log — used to dispute commission tracking with platforms."""
    db = get_db()
    try:
        db.table("affiliate_clicks").insert({
            "user_id":    user_id,
            "listing_id": listing_id,
            "platform":   platform,
            "ip_hash":    ip_hash,
            "clicked_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        log.warning("affiliate_click_log_failed", error=str(e))


# ── Scraper Run Logging ───────────────────────────────────────────────────────

async def log_scraper_run(
    platform: str,
    category: str,
    products_found: int,
    duration_seconds: float,
    status: str,
    error: Optional[str] = None,
) -> None:
    db = get_db()
    try:
        db.table("scraper_runs").insert({
            "platform":        platform,
            "category":        category,
            "products_found":  products_found,
            "duration_seconds": round(duration_seconds, 2),
            "status":          status,
            "error":           error,
            "ran_at":          datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        pass  # Non-fatal — don't crash scraper over logging
