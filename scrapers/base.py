"""
scrapers/base.py
────────────────
Abstract base class for all platform scrapers.
Handles: retry logic, polite delays, error classification, metrics.
Every scraper inherits from BaseScraper and implements scrape_category().
"""
from __future__ import annotations
import asyncio
import re
import time
from abc import ABC, abstractmethod
from typing import Optional

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import logging

from core.config import get_settings
from core.exceptions import ScraperError, ScraperRateLimited, ScraperStructureChanged
from core.logger import get_logger
from core.models import Platform, ScrapedProduct

log = get_logger("scraper.base")


class BaseScraper(ABC):
    """
    Base class for all platform scrapers.

    Subclasses must implement:
        platform: Platform (class attribute)
        scrape_category(category: str) -> list[ScrapedProduct]
    """

    platform: Platform  # Must be set by subclass

    def __init__(self):
        self.settings = get_settings()
        self._log = get_logger(f"scraper.{self.platform.value}")

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(self, categories: list[str]) -> list[ScrapedProduct]:
        """
        Run scraper across all given categories.
        Applies polite delay between categories.
        Returns combined de-duplicated product list.
        """
        all_products: list[ScrapedProduct] = []
        seen_ids: set[str] = set()

        for i, category in enumerate(categories):
            if i > 0:
                delay = self.settings.request_delay_seconds
                self._log.debug("polite_delay", seconds=delay, next_category=category)
                await asyncio.sleep(delay)

            start = time.monotonic()
            try:
                products = await self._scrape_with_retry(category)
                duration = round(time.monotonic() - start, 2)

                # De-duplicate by external_id
                new = [p for p in products if p.external_id not in seen_ids]
                seen_ids.update(p.external_id for p in new)
                all_products.extend(new)

                self._log.info(
                    "category_done",
                    platform=self.platform.value,
                    category=category,
                    products=len(new),
                    duration_s=duration,
                )
            except ScraperRateLimited as e:
                self._log.warning("rate_limited_skipping", category=category,
                                  platform=self.platform.value)
                # Back off for 2 minutes if rate-limited
                await asyncio.sleep(120)
            except ScraperStructureChanged as e:
                self._log.error("structure_changed",
                                platform=self.platform.value,
                                category=category,
                                msg="CSS selectors found nothing — site updated?")
            except ScraperError as e:
                self._log.error("scrape_failed",
                                platform=self.platform.value,
                                category=category,
                                reason=e.reason)

        self._log.info(
            "platform_done",
            platform=self.platform.value,
            total_products=len(all_products),
            categories_scraped=len(categories),
        )
        return all_products

    # ── Retry Wrapper ─────────────────────────────────────────────────────────

    async def _scrape_with_retry(self, category: str) -> list[ScrapedProduct]:
        """3 attempts with exponential backoff — skips retry for structure errors."""
        attempts = 0
        last_err = None
        for attempt in range(3):
            try:
                products = await self.scrape_category(category)
                cap = self.settings.max_products_per_category
                return products[:cap]
            except ScraperStructureChanged:
                raise  # Don't retry — needs code fix
            except ScraperRateLimited:
                raise  # Don't retry immediately — caller handles
            except ScraperError as e:
                last_err = e
                wait = 2 ** attempt * 3  # 3s, 6s, 12s
                self._log.warning("retry", attempt=attempt + 1, wait_s=wait, reason=e.reason)
                await asyncio.sleep(wait)
        raise last_err or ScraperError(self.platform.value, "max retries exceeded")

    # ── Abstract ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def scrape_category(self, category: str) -> list[ScrapedProduct]:
        """Fetch and parse products for one category. Must be overridden."""
        ...

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def extract_price(text: str) -> float:
        """Parse '₹1,299' or 'Rs. 1299.00' → 1299.0"""
        if not text:
            return 0.0
        cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @staticmethod
    def extract_int(text: str) -> int:
        cleaned = re.sub(r"[^\d]", "", text or "")
        return int(cleaned) if cleaned else 0

    @staticmethod
    def clean_title(text: str) -> str:
        if not text:
            return ""
        return " ".join(text.strip().split())  # Normalise whitespace

    @staticmethod
    def safe_url(url: str, base: str = "") -> str:
        if not url:
            return ""
        if url.startswith("http"):
            return url
        if url.startswith("//"):
            return "https:" + url
        return base.rstrip("/") + "/" + url.lstrip("/")
