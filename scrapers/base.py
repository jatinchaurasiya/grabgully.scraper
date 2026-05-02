"""
scrapers/base.py
────────────────
Abstract base class for all platform scrapers.
Handles: retry logic, polite delays, error classification.

Scrapling 0.4.5 API notes:
  - Import: from scrapling.fetchers.stealth_chrome import StealthyFetcher
  - Fetch:  StealthyFetcher.fetch(url, wait_selector="...", timeout=20000)
  - css_first: use page.css(selector).first  (NOT page.css_first())
  - html:   page.html  (string of full page HTML)
  - attrib: element.attrib["href"]  or  element.attrib.get("href", "")
"""
from __future__ import annotations
import asyncio
import re
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from core.config import get_settings
from core.exceptions import ScraperError, ScraperRateLimited, ScraperStructureChanged
from core.logger import get_logger
from core.models import Platform, ScrapedProduct

log = get_logger("scraper.base")

# Thread pool for running sync Scrapling calls inside async context.
# One worker per platform so no scraper queues behind another.
_executor = ThreadPoolExecutor(
    max_workers=5,
    thread_name_prefix="scraper",
)

# ── Shared browser settings (memory optimisation for Railway 512 MB) ──────────
# Pass these into every DynamicFetcher.fetch() call via **BROWSER_KWARGS.
BROWSER_ARGS = [
    "--disable-extensions",
    "--disable-gpu",
    "--no-zygote",
    "--single-process",
    "--disable-dev-shm-usage",
    "--memory-pressure-off",
]
BROWSER_VIEWPORT = {"width": 800, "height": 600}
# Usage in subclasses:
#   from scrapers.base import BROWSER_ARGS, BROWSER_VIEWPORT
#   DynamicFetcher.fetch(url, ..., extra_args=BROWSER_ARGS, viewport=BROWSER_VIEWPORT)


class BaseScraper(ABC):
    """
    Base class for all platform scrapers.

    Scrapling 0.4.5 is synchronous — we run it in a thread executor
    so it doesn't block the FastAPI async event loop.

    Subclasses must define:
        platform: Platform   (class attribute)
        scrape_category(category: str) -> list[ScrapedProduct]
    """

    platform: Platform

    def __init__(self):
        self.settings = get_settings()
        self._log = get_logger(f"scraper.{self.platform.value}")

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(self, categories: list[str]) -> list[ScrapedProduct]:
        """
        Run scraper across all given categories with polite delays.
        Returns combined de-duplicated product list.
        """
        all_products: list[ScrapedProduct] = []
        seen: set[str] = set()

        for i, category in enumerate(categories):
            if i > 0:
                delay = self.settings.request_delay_seconds
                self._log.debug("polite_delay", seconds=delay, next=category)
                await asyncio.sleep(delay)

            start = time.monotonic()
            try:
                products = await self._scrape_with_retry(category)
                dur = round(time.monotonic() - start, 2)
                new = [p for p in products if p.external_id not in seen]
                seen.update(p.external_id for p in new)
                all_products.extend(new)
                self._log.info("category_done", platform=self.platform.value,
                               category=category, products=len(new), duration_s=dur)
            except ScraperRateLimited:
                self._log.warning("rate_limited_skip", category=category)
                await asyncio.sleep(120)
            except ScraperStructureChanged as e:
                self._log.error("structure_changed", category=category, reason=e.reason)
            except ScraperError as e:
                self._log.error("category_failed", category=category, reason=e.reason)

        self._log.info("platform_done", platform=self.platform.value,
                       total=len(all_products), categories=len(categories))
        return all_products

    # ── Retry wrapper ─────────────────────────────────────────────────────────

    async def _scrape_with_retry(self, category: str) -> list[ScrapedProduct]:
        """3 attempts with exponential back-off. Does NOT retry structure errors."""
        last_err = None
        for attempt in range(3):
            try:
                products = await asyncio.get_event_loop().run_in_executor(
                    _executor, self._sync_scrape, category
                )
                cap = self.settings.max_products_per_category
                return products[:cap]
            except (ScraperStructureChanged, ScraperRateLimited):
                raise
            except ScraperError as e:
                last_err = e
                wait = 3 * (2 ** attempt)   # 3s, 6s, 12s
                self._log.warning("retry", attempt=attempt + 1, wait_s=wait, reason=e.reason)
                await asyncio.sleep(wait)
        raise last_err or ScraperError(self.platform.value, "max retries exceeded")

    def _sync_scrape(self, category: str) -> list[ScrapedProduct]:
        """
        Synchronous wrapper called from the thread executor.
        Delegates to the subclass scrape_category() implementation.
        """
        return self.scrape_category(category)

    # ── Abstract ──────────────────────────────────────────────────────────────

    @abstractmethod
    def scrape_category(self, category: str) -> list[ScrapedProduct]:
        """
        Synchronous scrape for one category.
        Called in a thread pool — safe to use blocking Scrapling calls.
        Returns list of ScrapedProduct.
        """
        ...

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def extract_price(text: str) -> float:
        if not text:
            return 0.0
        cleaned = re.sub(r"[^\d.]", "", str(text).replace(",", ""))
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @staticmethod
    def extract_int(text: str) -> int:
        cleaned = re.sub(r"[^\d]", "", str(text or ""))
        return int(cleaned) if cleaned else 0

    @staticmethod
    def clean_title(text: str) -> str:
        if not text:
            return ""
        return " ".join(str(text).strip().split())

    @staticmethod
    def safe_url(url: str, base: str = "") -> str:
        if not url:
            return ""
        url = str(url)
        if url.startswith("http"):
            return url
        if url.startswith("//"):
            return "https:" + url
        return base.rstrip("/") + "/" + url.lstrip("/")

    @staticmethod
    def css_first(element, selector: str):
        """
        Helper: returns the first match for a CSS selector, or None.
        Scrapling 0.4.5: use .css(selector).first  (no css_first method)
        """
        try:
            results = element.css(selector)
            return results.first if results else None
        except Exception:
            return None
