"""
scrapers/snapdeal.py
────────────────────
Snapdeal scraper — Scrapling 0.4.5 + DynamicFetcher.
Snapdeal has some server-side rendering but still needs a browser for JS.
"""
from __future__ import annotations
from scrapling.fetchers.chrome import DynamicFetcher

from core.exceptions import ScraperError, ScraperRateLimited, ScraperStructureChanged
from core.models import Platform, ScrapedProduct
from scrapers.base import BaseScraper
from integrations.affiliate import build_snapdeal_affiliate_url

SNAPDEAL_BASE = "https://www.snapdeal.com"

CATEGORY_MAP = {
    "smartphones":  "mobile-phones",
    "earphones":    "earphones-headphones",
    "powerbanks":   "power-banks",
    "watches":      "watches",
    "bags":         "bags-luggage",
    "tshirts":      "mens-t-shirts",
    "sneakers":     "sports-shoes",
    "kitchen":      "kitchen-storage",
    "toys":         "toys-games",
}


class SnapdealScraper(BaseScraper):
    platform = Platform.SNAPDEAL

    def scrape_category(self, category: str) -> list[ScrapedProduct]:
        slug = CATEGORY_MAP.get(category, category)
        url  = (
            f"{SNAPDEAL_BASE}/products/{slug}"
            f"?sort=rlvncy&rating=3&discount=10&q={category}"
        )
        self._log.info("scraping", platform="snapdeal", url=url)

        try:
            page = DynamicFetcher.fetch(
                url,
                headless=True,
                wait_selector=".product-tuple-listing",
                timeout=20000,
                disable_resources=True,
            )
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "blocked" in err:
                raise ScraperRateLimited("snapdeal", "blocked")
            raise ScraperError("snapdeal", str(e))

        items = page.css(".product-tuple-listing")
        if not items:
            items = page.css(".favDp")
            if not items:
                raise ScraperStructureChanged("snapdeal", "product listing selector not found")

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
        title_el = (
            self.css_first(item, ".product-title")
            or self.css_first(item, "p.product-title")
        )
        title = self.clean_title(title_el.text if title_el else "")
        if not title:
            return None

        price_el = self.css_first(item, ".product-price")
        price    = self.extract_price(price_el.text if price_el else "")
        if price <= 0:
            return None

        orig_el  = self.css_first(item, ".product-desc-price.strike")
        orig     = self.extract_price(orig_el.text if orig_el else "")

        disc_el  = self.css_first(item, ".product-discount span")
        disc     = self.extract_int(disc_el.text if disc_el else "")

        img_el   = self.css_first(item, "img.product-image") or self.css_first(item, "img")
        img_url  = img_el.attrib.get("src", "") if img_el else ""

        link_el  = self.css_first(item, "a.dp-widget-link") or self.css_first(item, "a")
        href     = link_el.attrib.get("href", "") if link_el else ""
        prod_url = self.safe_url(href, SNAPDEAL_BASE)

        parts    = href.rstrip("/").split("/")
        prod_id  = parts[-1] if (parts and parts[-1].isdigit()) else href[:32]
        if not prod_id:
            prod_id = title[:24].replace(" ", "_")

        return ScrapedProduct(
            external_id    = prod_id,
            platform       = Platform.SNAPDEAL,
            title          = title,
            brand          = "",
            image_url      = img_url,
            current_price  = price,
            original_price = orig or price,
            discount_pct   = disc,
            affiliate_url  = build_snapdeal_affiliate_url(prod_url),
            category       = category,
        )
