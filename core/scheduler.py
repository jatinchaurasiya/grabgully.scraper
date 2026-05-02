"""
core/scheduler.py
──────────────────
APScheduler setup. All cron jobs in one place.

Schedule (IST, 6 AM–11 PM only — saves Railway free credits):
  Every 30 min, :00 & :30  → _run_scrapers()         Flipkart/Myntra/Meesho
  Every 30 min, :15 & :45  → _run_amazon_creator()   Amazon Creator API refresh
  Every 15 min              → _run_price_check()      FCM alerts for watchlist hits
  Daily at 2:00 AM          → _cleanup_old_data()     Delete price_history > 1 year
"""
from __future__ import annotations
import asyncio
import time
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from core.config import get_settings
from core.logger import get_logger

log = get_logger("scheduler")

# ── Categories scraped on every run ──────────────────────────────────────────
SCRAPE_CATEGORIES = [
    "smartphones",
    "earphones",
    "kurta",
    "sneakers",
    "jeans",
]

# Amazon Creator API search keywords
AMAZON_KEYWORDS = [
    "smartphones under 15000",
    "truly wireless earbuds",
    "laptops under 50000",
    "men kurta",
    "running shoes",
    "women bags",
    "smartwatch",
    "best sellers electronics",
]


def create_scheduler() -> AsyncIOScheduler:
    s = get_settings()
    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

    # ── All platform scrapers: every 30 min ──────────────────────────────────
    scheduler.add_job(
        _run_scrapers,
        CronTrigger(
            hour=f"{s.scrape_start_hour}-{s.scrape_end_hour}",
            minute="*/30",
            timezone="Asia/Kolkata",
        ),
        id="scrapers",
        name="Platform Scrapers (Scrapling)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    # ── Amazon Creator API: offset by 15 min so it never clashes with scrapers
    scheduler.add_job(
        _run_amazon_creator,
        CronTrigger(
            hour=f"{s.scrape_start_hour}-{s.scrape_end_hour}",
            minute="15,45",
            timezone="Asia/Kolkata",
        ),
        id="amazon_creator",
        name="Amazon Creator API",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    # ── Price drop alerts: every 15 min ─────────────────────────────────────
    scheduler.add_job(
        _run_price_check,
        CronTrigger(minute="*/15", timezone="Asia/Kolkata"),
        id="price_alerts",
        name="Price Drop Alerts (FCM)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )

    # ── Daily cleanup at 2 AM IST ────────────────────────────────────────────
    scheduler.add_job(
        _cleanup_old_data,
        CronTrigger(hour=2, minute=0, timezone="Asia/Kolkata"),
        id="cleanup",
        name="Daily Cleanup",
        max_instances=1,
    )

    return scheduler


# ── Job: Platform Scrapers ────────────────────────────────────────────────────

async def _run_scrapers() -> None:
    """
    Run all platform scrapers (Scrapling + Playwright).
    Runs sequentially with polite delays between platforms.
    Includes Flipkart — now scraped directly, not via API.
    """
    from scrapers import (
        FlipkartScraper, MyntraScraper, MeeshoScraper,
    )
    from services.db import upsert_listings_bulk, log_scraper_run

    scrapers = [
        FlipkartScraper(),
        MyntraScraper(),
        MeeshoScraper(),
    ]

    async def run_one(scraper) -> int:
        """Run a single scraper with its own error boundary."""
        start = time.monotonic()
        try:
            products = await scraper.run(SCRAPE_CATEGORIES)
            count    = await upsert_listings_bulk(products)
            duration = time.monotonic() - start
            await log_scraper_run(
                platform         = scraper.platform.value,
                category         = "all",
                products_found   = count,
                duration_seconds = duration,
                status           = "success",
            )
            log.info("scraper_done", platform=scraper.platform.value,
                     products=count, duration_s=round(duration, 1))
            return count
        except Exception as e:
            duration = time.monotonic() - start
            await log_scraper_run(
                platform         = scraper.platform.value,
                category         = "all",
                products_found   = 0,
                duration_seconds = duration,
                status           = "failed",
                error            = str(e)[:500],
            )
            log.error("scraper_failed", platform=scraper.platform.value, error=str(e))
            return 0

    # Run all scrapers sequentially to prevent OOM crashes on Railway (512MB limit)
    results = []
    for s in scrapers:
        result = await run_one(s)
        results.append(result)
    log.info("all_scrapers_done", total_products=sum(results))


# ── Job: Amazon Creator API ───────────────────────────────────────────────────

async def _run_amazon_creator() -> None:
    """
    Fetch live Amazon product data via the Amazon Creator API (OAuth2 LWA).
    Only runs when AMAZON_CLIENT_ID + AMAZON_CLIENT_SECRET are configured.
    """
    from integrations.amazon import get_amazon
    from services.db import upsert_listings_bulk

    s = get_settings()
    if not s.amazon_configured:
        log.debug("amazon_creator_skip", reason="not configured — skipping")
        return

    all_products = []
    amazon = get_amazon()

    # Search products by keyword
    for keyword in AMAZON_KEYWORDS:
        try:
            products = await amazon.search_products(keyword, count=10)
            all_products.extend(products)
            await asyncio.sleep(1.1)   # Creator API: max ~1 req/sec
        except Exception as e:
            log.warning("amazon_keyword_failed", keyword=keyword, error=str(e))

    # Also pull current deals
    try:
        deals = await amazon.get_deals(count=20)
        all_products.extend(deals)
    except Exception as e:
        log.warning("amazon_deals_failed", error=str(e))

    if all_products:
        count = await upsert_listings_bulk(all_products)
        log.info("amazon_creator_done", products=count)


# ── Job: Price Drop Alerts ────────────────────────────────────────────────────

async def _run_price_check() -> None:
    """Check watchlist target prices and fire FCM push notifications."""
    from services.price_tracker import check_price_drops
    fired = await check_price_drops()
    if fired:
        log.info("price_alerts_fired", count=fired)


# ── Job: Daily Cleanup ────────────────────────────────────────────────────────

async def _cleanup_old_data() -> None:
    """Delete price_history rows older than 365 days to keep DB lean."""
    from services.db import get_db
    from datetime import timedelta
    db = get_db()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        db.table("price_history").delete().lt("scraped_at", cutoff).execute()
        log.info("cleanup_done", cutoff=cutoff)
    except Exception as e:
        log.error("cleanup_failed", error=str(e))
