"""
core/scheduler.py
──────────────────
APScheduler setup. All cron jobs defined here — one place to see
the full scraping schedule.

Schedule (IST timezone, 6 AM–11 PM only to save Railway credits):
  Every 30 min  → run_all_scrapers()    — scrape Myntra, Meesho, Ajio, Snapdeal
  Every 30 min  → run_api_integrations() — refresh Amazon + Flipkart via API
  Every 15 min  → check_price_drops()   — fire FCM alerts for watchlist hits
  Daily 2 AM    → cleanup_old_history() — delete price_history older than 1 year
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

# Category list scraped on every run
SCRAPE_CATEGORIES = [
    "smartphones",
    "earphones",
    "laptops",
    "kurta",
    "sneakers",
    "jeans",
    "bags",
    "watches",
    "skincare",
    "tshirts",
]

# Amazon keyword searches (PA-API)
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

    # Scraper cron: every 30 min between start_hour and end_hour IST
    scheduler.add_job(
        _run_scrapers,
        CronTrigger(
            hour=f"{s.scrape_start_hour}-{s.scrape_end_hour}",
            minute="*/30",
            timezone="Asia/Kolkata",
        ),
        id="scrapers",
        name="Platform Scrapers",
        max_instances=1,   # Never run two scrape jobs simultaneously
        coalesce=True,
        misfire_grace_time=300,
    )

    # Amazon + Flipkart API refresh: every 30 min
    scheduler.add_job(
        _run_api_integrations,
        CronTrigger(
            hour=f"{s.scrape_start_hour}-{s.scrape_end_hour}",
            minute="15,45",   # Offset from scrapers to spread load
            timezone="Asia/Kolkata",
        ),
        id="api_integrations",
        name="Amazon + Flipkart API",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    # Price drop alerts: every 15 min
    scheduler.add_job(
        _run_price_check,
        CronTrigger(minute="*/15", timezone="Asia/Kolkata"),
        id="price_alerts",
        name="Price Drop Alerts",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )

    # Daily cleanup at 2 AM IST
    scheduler.add_job(
        _cleanup_old_data,
        CronTrigger(hour=2, minute=0, timezone="Asia/Kolkata"),
        id="cleanup",
        name="Daily Cleanup",
        max_instances=1,
    )

    return scheduler


# ── Job implementations ───────────────────────────────────────────────────────

async def _run_scrapers() -> None:
    """Run all platform scrapers sequentially with delays."""
    from scrapers import MyntraScraper, MeeshoScraper, AjioScraper, SnapdealScraper
    from services.db import upsert_listings_bulk, record_price_history, log_scraper_run

    scrapers = [
        MyntraScraper(),
        MeeshoScraper(),
        AjioScraper(),
        SnapdealScraper(),
    ]

    total = 0
    for scraper in scrapers:
        start = time.monotonic()
        try:
            products = await scraper.run(SCRAPE_CATEGORIES)
            count    = await upsert_listings_bulk(products)
            duration = time.monotonic() - start
            total   += count

            await log_scraper_run(
                platform       = scraper.platform.value,
                category       = "all",
                products_found = count,
                duration_seconds = duration,
                status         = "success",
            )
            log.info("scraper_done",
                     platform=scraper.platform.value,
                     products=count, duration=round(duration, 1))

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
            log.error("scraper_failed",
                      platform=scraper.platform.value, error=str(e))

        # Respectful delay between platforms
        await asyncio.sleep(get_settings().request_delay_seconds * 2)

    log.info("all_scrapers_done", total_products=total)


async def _run_api_integrations() -> None:
    """Fetch live data from Amazon PA-API and Flipkart Affiliate API."""
    from integrations.amazon import get_amazon
    from integrations.flipkart import get_flipkart
    from services.db import upsert_listings_bulk

    s = get_settings()

    # Amazon
    if s.amazon_configured:
        all_products = []
        for keyword in AMAZON_KEYWORDS:
            try:
                products = await get_amazon().search_items(keyword, count=10)
                all_products.extend(products)
                await asyncio.sleep(1.1)   # PA-API: max 1 req/sec
            except Exception as e:
                log.warning("amazon_keyword_failed", keyword=keyword, error=str(e))

        if all_products:
            count = await upsert_listings_bulk(all_products)
            log.info("amazon_done", products=count)

    # Flipkart
    if s.flipkart_configured:
        fk_products = []
        for keyword in AMAZON_KEYWORDS[:5]:   # Fewer FK queries
            try:
                products = await get_flipkart().search_products(keyword, count=10)
                fk_products.extend(products)
                await asyncio.sleep(0.5)
            except Exception as e:
                log.warning("flipkart_keyword_failed", keyword=keyword, error=str(e))

        if fk_products:
            count = await upsert_listings_bulk(fk_products)
            log.info("flipkart_done", products=count)


async def _run_price_check() -> None:
    from services.price_tracker import check_price_drops
    fired = await check_price_drops()
    if fired:
        log.info("price_alerts_fired", count=fired)


async def _cleanup_old_data() -> None:
    """Delete price_history older than 365 days to keep DB lean."""
    from services.db import get_db
    from datetime import timedelta
    db = get_db()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        db.table("price_history").delete().lt("scraped_at", cutoff).execute()
        log.info("cleanup_done", cutoff=cutoff)
    except Exception as e:
        log.error("cleanup_failed", error=str(e))
