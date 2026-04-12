"""
scrapers/snapdeal.py
────────────────────
Snapdeal scraper — lower-ticket electronics and fashion.
Snapdeal uses server-side rendering, so Scrapling's lightweight fetcher
works without full Playwright in many cases.
Affiliate links are generated via CueLink (build_snapdeal_affiliate_url).
"""
from __future__ import annotations
from scrapling.auto import Fetcher
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

    async def scrape_category(self, category: str) -> list[ScrapedProduct]:
        slug = CATEGORY_MAP.get(category, category)
        url  = (
            f"{SNAPDEAL_BASE}/products/{slug}"
            f"?sort=rlvncy&rating=3&discount=10&q={category}"
        )

        self._log.info("scraping", platform="snapdeal", url=url)

        try:
            # Try lightweight fetch first (Snapdeal has some SSR)
            fetcher = Fetcher(auto_match=False, stealth=True)
            page = fetcher.get(url, stealthy_headers=True, wait_timeout=15000)
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "blocked" in err_str:
                raise ScraperRateLimited("snapdeal", "blocked")
            raise ScraperError("snapdeal", str(e))

        items = page.css(".product-tuple-listing")
        if not items:
            items = page.css(".favDp")
            if not items:
                raise ScraperStructureChanged("snapdeal", "product listing selector not found")

        products = []
        for item in items:
            try:
                p = await self._parse_product(item, category)
                if p:
                    products.append(p)
            except Exception:
                continue

        return products

    async def _parse_product(self, item, category: str) -> ScrapedProduct | None:
        title_el = item.css_first(".product-title") or item.css_first("p.product-title")
        title    = self.clean_title(title_el.text if title_el else "")
        if not title:
            return None

        price_el = item.css_first(".product-price")
        price    = self.extract_price(price_el.text if price_el else "")
        if price <= 0:
            return None

        orig_el  = item.css_first(".product-desc-price.strike")
        orig     = self.extract_price(orig_el.text if orig_el else "")

        disc_el  = item.css_first(".product-discount span")
        disc     = self.extract_int(disc_el.text if disc_el else "")

        img_el   = item.css_first("img.product-image")
        img_url  = img_el.attrib.get("src", "") if img_el else ""

        link_el  = item.css_first("a.dp-widget-link")
        href     = link_el.attrib.get("href", "") if link_el else ""
        prod_url = self.safe_url(href, SNAPDEAL_BASE)

        # Snapdeal product IDs appear in URL as /product/{name}/{id}
        parts    = href.rstrip("/").split("/")
        prod_id  = parts[-1] if parts and parts[-1].isdigit() else href[:32]

        return ScrapedProduct(
            external_id    = prod_id,
            platform       = Platform.SNAPDEAL,
            title          = title,
            brand          = "",
            image_url      = img_url,
            current_price  = price,
            original_price = orig or price,
            discount_pct   = disc,
            affiliate_url  = await build_snapdeal_affiliate_url(prod_url),
            category       = category,
        )
