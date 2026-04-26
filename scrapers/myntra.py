"""
scrapers/myntra.py
──────────────────
Myntra product scraper using Scrapling 0.4.5 + Playwright (DynamicFetcher).

Scrapling 0.4.5 API:
  DynamicFetcher.fetch(url, wait_selector=".cls", timeout=20000)
  css_first → use .css(sel).first
  .html     → full page HTML string
"""
from __future__ import annotations
import re
from scrapling.fetchers.chrome import DynamicFetcher

from core.exceptions import ScraperError, ScraperRateLimited, ScraperStructureChanged
from core.models import Platform, ScrapedProduct
from scrapers.base import BaseScraper
from integrations.affiliate import build_myntra_affiliate_url

MYNTRA_BASE = "https://www.myntra.com"

CATEGORY_MAP = {
    "smartphones":  "mobile-phones",
    "laptops":      "laptops",
    "earphones":    "earphones",
    "headphones":   "headphones",
    "kurta":        "kurtas",
    "jeans":        "jeans",
    "sneakers":     "sports-shoes",
    "tshirts":      "tshirts",
    "saree":        "sarees",
    "watches":      "watches",
    "bags":         "bags-wallets-belts",
    "skincare":     "skin-care",
}


class MyntraScraper(BaseScraper):
    platform = Platform.MYNTRA

    def scrape_category(self, category: str) -> list[ScrapedProduct]:
        slug = CATEGORY_MAP.get(category, category)
        url  = f"{MYNTRA_BASE}/{slug}?rawQuery={slug}&sort=popularity&rows=40"
        self._log.info("scraping", platform="myntra", url=url)

        try:
            page = DynamicFetcher.fetch(
                url,
                headless=True,
                wait_selector=".product-base",
                timeout=20000,
                disable_resources=True,
            )
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "too many" in err:
                raise ScraperRateLimited("myntra", "429 rate limit")
            if "timeout" in err:
                raise ScraperError("myntra", f"page timeout: {e}")
            raise ScraperError("myntra", str(e))

        html_lower = page.html.lower() if page.html else ""
        if "access denied" in html_lower or "captcha" in html_lower:
            raise ScraperRateLimited("myntra", "access denied or CAPTCHA")

        items = page.css(".product-base")
        if not items:
            raise ScraperStructureChanged("myntra", ".product-base found nothing")

        products: list[ScrapedProduct] = []
        for item in items:
            try:
                p = self._parse(item, category)
                if p:
                    products.append(p)
            except Exception:
                continue
        return products

    def _parse(self, item, category: str) -> ScrapedProduct | None:
        # Product ID — from data attribute or href
        pid = ""
        meta_el = self.css_first(item, ".product-productMetaInfo")
        if meta_el:
            pid = meta_el.attrib.get("data-id", "")
        if not pid:
            a_el = self.css_first(item, "a")
            if a_el:
                href = a_el.attrib.get("href", "")
                pid = href.rstrip("/").split("/")[-1].split("?")[0]
        if not pid:
            return None

        brand_el = self.css_first(item, ".product-brand")
        title_el = self.css_first(item, ".product-product")
        brand = self.clean_title(brand_el.text if brand_el else "")
        title = self.clean_title(title_el.text if title_el else "")
        if not title:
            return None
        full_title = f"{brand} {title}".strip()

        deal_el = self.css_first(item, ".product-discountedPrice")
        orig_el = self.css_first(item, ".product-strike")
        deal_price = self.extract_price(deal_el.text if deal_el else "")
        orig_price = self.extract_price(orig_el.text if orig_el else "")
        if deal_price <= 0:
            return None

        disc_el  = self.css_first(item, ".product-discountPercentage")
        discount = self.extract_int(disc_el.text if disc_el else "")

        img_el    = self.css_first(item, "img.img-responsive") or self.css_first(item, "img")
        image_url = img_el.attrib.get("src", "") if img_el else ""

        link_el   = self.css_first(item, "a")
        raw_url   = link_el.attrib.get("href", "") if link_el else ""
        prod_url  = self.safe_url(raw_url, MYNTRA_BASE)

        return ScrapedProduct(
            external_id    = str(pid),
            platform       = Platform.MYNTRA,
            title          = full_title,
            brand          = brand,
            image_url      = image_url,
            current_price  = deal_price,
            original_price = orig_price or deal_price,
            discount_pct   = discount,
            affiliate_url  = build_myntra_affiliate_url(prod_url),
            category       = category,
        )
