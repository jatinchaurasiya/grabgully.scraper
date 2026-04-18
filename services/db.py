"""
services/db.py
──────────────
All Supabase interactions in one place.
Repositories pattern — no raw Supabase calls anywhere else.

The Supabase Python SDK is synchronous. Every .execute() call is wrapped
in _run_sync() so it runs on a dedicated thread pool and never blocks the
FastAPI async event loop.
"""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from datetime import datetime, timezone
from typing import Optional

from supabase import create_client, Client

from core.config import get_settings
from core.exceptions import DatabaseError
from core.logger import get_logger
from core.models import ScrapedProduct, PriceAlert

log = get_logger("db")

# Dedicated thread pool for blocking Supabase I/O.
# 4 workers: enough for concurrent API requests without exhausting DB connections.
_db_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="supabase")


async def _run_sync(fn):
    """
    Run a blocking Supabase call off the async event loop.
    `fn` should be a zero-argument callable (use lambdas to capture args).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_db_executor, fn)


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
        res = await _run_sync(
            lambda: db.table("platform_listings")
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
    """
    Bulk upsert — batches of 100 rows. Returns count of successful upserts.

    After each batch, records price_history for every listing whose price
    changed (or has no history yet). This feeds the Vico chart in
    CompareScreen and the 24h price-drop calculation.
    """
    if not products:
        return 0

    db = get_db()
    batch_size   = 100
    success_count = 0

    # Map external_id → current_price so we can look up the scraped price
    # from the upsert response (which returns the DB row's external_id).
    price_by_ext_id: dict[str, float] = {
        p.external_id: p.current_price for p in products
    }

    # Build all rows upfront
    rows = [
        {
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
        }
        for p in products
    ]

    # (listing_id, current_price) of every successfully upserted row
    upserted_pairs: list[tuple[str, float]] = []

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        try:
            res = await _run_sync(
                lambda b=batch: db.table("platform_listings")
                                  .upsert(b, on_conflict="platform,external_id")
                                  .execute()
            )
            success_count += len(res.data or [])

            # Collect (listing_id, scraped_price) for price history step
            for row_data in (res.data or []):
                lid     = row_data.get("id")
                ext_id  = row_data.get("external_id", "")
                # Prefer the in-memory scraped price; fall back to DB value
                price   = price_by_ext_id.get(ext_id, row_data.get("current_price", 0.0))
                if lid:
                    upserted_pairs.append((lid, float(price)))

        except Exception as e:
            log.error("bulk_upsert_failed", batch_start=i, error=str(e))

    # ── Record price history for changed prices ──────────────────────────────
    # For each upserted listing, query the latest known price.
    # Only write a new row if the price changed (or no history exists yet).
    history_written = 0
    for listing_id, current_price in upserted_pairs:
        try:
            last_res = await _run_sync(
                lambda lid=listing_id: db.table("price_history")
                                         .select("price")
                                         .eq("listing_id", lid)
                                         .order("scraped_at", desc=True)
                                         .limit(1)
                                         .execute()
            )
            last_data  = last_res.data or []
            last_price = float(last_data[0]["price"]) if last_data else None

            # Write if: first-ever record OR price moved by at least 1 paisa
            if last_price is None or abs(last_price - current_price) >= 0.01:
                await record_price_history(listing_id, current_price)
                history_written += 1

        except Exception as e:
            log.warning("price_history_check_failed",
                        listing_id=listing_id, error=str(e))

    if history_written:
        log.info("price_history_written", count=history_written,
                 of_total=len(upserted_pairs))

    return success_count


async def record_price_history(listing_id: str, price: float) -> None:
    """Append a price point to the price_history table."""
    db = get_db()
    try:
        await _run_sync(
            lambda: db.table("price_history").insert({
                "listing_id": listing_id,
                "price":      price,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        )
    except Exception as e:
        log.warning("price_history_insert_failed",
                    listing_id=listing_id, error=str(e))


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
        res = await _run_sync(
            lambda: db.table("watchlist")
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
        await _run_sync(
            lambda: db.table("watchlist")
                      .update({
                          "is_notified": True,
                          "notified_at": datetime.now(timezone.utc).isoformat(),
                      })
                      .eq("id", watchlist_id)
                      .execute()
        )
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
        await _run_sync(
            lambda: db.table("affiliate_clicks").insert({
                "user_id":    user_id,
                "listing_id": listing_id,
                "platform":   platform,
                "ip_hash":    ip_hash,
                "clicked_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        )
    except Exception as e:
        log.warning("affiliate_click_log_failed", error=str(e))


# ── XP ────────────────────────────────────────────────────────────────────────

async def award_xp(
    user_id:    str,
    action_type: str,
    xp:         int,
    metadata:   dict = None,
) -> None:
    """
    Award XP to a user and append an audit row to xp_events.
    Uses an atomic Supabase RPC call to avoid race conditions.

    Args:
        user_id:     Supabase auth user UUID.
        action_type: Event label (e.g. "affiliate_click", "watchlist_add").
        xp:          Amount to award. Must be > 0.
        metadata:    Optional JSON payload stored on the xp_events row.
    """
    if xp <= 0:
        return
    db = get_db()
    try:
        await _run_sync(
            lambda: db.table("xp_events").insert({
                "user_id":     user_id,
                "action_type": action_type,
                "xp_amount":   xp,
                "metadata":    metadata or {},
            }).execute()
        )
        await _run_sync(
            lambda: db.rpc(
                "increment_user_xp",
                {"p_user_id": user_id, "p_xp": xp},
            ).execute()
        )
    except Exception as e:
        log.warning("xp_award_failed", user_id=user_id,
                    action_type=action_type, error=str(e))


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
        await _run_sync(
            lambda: db.table("scraper_runs").insert({
                "platform":         platform,
                "category":         category,
                "products_found":   products_found,
                "duration_seconds": round(duration_seconds, 2),
                "status":           status,
                "error":            error,
                "ran_at":           datetime.now(timezone.utc).isoformat(),
            }).execute()
        )
    except Exception:
        pass  # Non-fatal — don't crash scraper over logging
