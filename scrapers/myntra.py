"""
scrapers/myntra.py
──────────────────
Myntra product scraper using Scrapling + Playwright.
Myntra is a JS-heavy SPA — we need a headless browser.

Category URL format: https://www.myntra.com/{category}?rawQuery={category}&sort=popularity
"""
from __future__ import annotations
import asyncio
from scrapling.auto import Fetcher
from core.exceptions import ScraperError, ScraperRateLimited, ScraperStructureChanged
from core.models import Platform, ScrapedProduct
from core.config import get_settings
from scrapers.base import BaseScraper
from integrations.affiliate import build_myntra_affiliate_url

MYNTRA_BASE = "https://www.myntra.com"

# Map our category names to Myntra URL slugs
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

    async def scrape_category(self, category: str) -> list[ScrapedProduct]:
        slug = CATEGORY_MAP.get(category, category)
        url  = f"{MYNTRA_BASE}/{slug}?rawQuery={slug}&sort=popularity&rows=40"

        self._log.info("scraping", platform="myntra", url=url)

        try:
            # Scrapling Fetcher with stealth mode — bypasses basic bot detection
            fetcher = Fetcher(auto_match=True, stealth=True)
            page = fetcher.get(
                url,
                stealthy_headers=True,
                wait_selector=".product-base",
                wait_timeout=15000,   # 15 seconds max
            )
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "too many" in err_str:
                raise ScraperRateLimited("myntra", "429 rate limit")
            if "timeout" in err_str:
                raise ScraperError("myntra", f"page timeout: {e}")
            raise ScraperError("myntra", str(e))

        # Check for CAPTCHA / access denied
        if page.css(".error-404") or "access denied" in page.html.lower():
            raise ScraperRateLimited("myntra", "access denied or CAPTCHA")

        items = page.css(".product-base")
        if not items:
            raise ScraperStructureChanged("myntra", ".product-base selector returned nothing")

        products: list[ScrapedProduct] = []
        for item in items:
            try:
                product = self._parse_product(item, category)
                if product:
                    products.append(product)
            except Exception as e:
                self._log.debug("parse_skip", reason=str(e))
                continue

        return products

    def _parse_product(self, item, category: str) -> ScrapedProduct | None:
        # Product ID
        product_id = (
            item.css_first(".product-productMetaInfo")
            and item.css_first(".product-productMetaInfo").attrib.get("data-id", "")
            or item.css_first("a")
            and item.css_first("a").attrib.get("href", "").split("/")[-1].split("?")[0]
        )
        if not product_id:
            return None

        # Title + Brand
        brand_el = item.css_first(".product-brand")
        title_el = item.css_first(".product-product")
        brand = self.clean_title(brand_el.text if brand_el else "")
        title = self.clean_title(title_el.text if title_el else "")
        if not title:
            return None
        full_title = f"{brand} {title}".strip()

        # Prices
        deal_el = item.css_first(".product-discountedPrice")
        orig_el = item.css_first(".product-strike")
        deal_price = self.extract_price(deal_el.text if deal_el else "")
        orig_price = self.extract_price(orig_el.text if orig_el else "")

        if deal_price <= 0:
            return None

        # Discount
        disc_el = item.css_first(".product-discountPercentage")
        discount = self.extract_int(disc_el.text if disc_el else "")

        # Image
        img_el = item.css_first("img.img-responsive")
        image_url = img_el.attrib.get("src", "") if img_el else ""

        # Product URL
        link_el = item.css_first("a")
        raw_url  = link_el.attrib.get("href", "") if link_el else ""
        product_url = self.safe_url(raw_url, MYNTRA_BASE)

        # Affiliate URL
        affiliate_url = build_myntra_affiliate_url(product_url)

        return ScrapedProduct(
            external_id    = str(product_id),
            platform       = Platform.MYNTRA,
            title          = full_title,
            brand          = brand,
            image_url      = image_url,
            current_price  = deal_price,
            original_price = orig_price or deal_price,
            discount_pct   = discount,
            affiliate_url  = affiliate_url,
            category       = category,
        )
