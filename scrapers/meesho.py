"""
scrapers/meesho.py
──────────────────
Meesho scraper. Meesho is React-based — uses Scrapling with Playwright.
Affiliate links are generated via CueLink (build_meesho_affiliate_url).
Category URLs follow: https://meesho.com/search?q={keyword}&page=1
"""
from __future__ import annotations
from scrapling.auto import Fetcher
from core.exceptions import ScraperError, ScraperRateLimited, ScraperStructureChanged
from core.models import Platform, ScrapedProduct
from scrapers.base import BaseScraper
from integrations.affiliate import build_meesho_affiliate_url

MEESHO_BASE = "https://meesho.com"

CATEGORY_MAP = {
    "kurta":        "kurta for women",
    "saree":        "sarees",
    "jeans":        "women jeans",
    "tshirts":      "men tshirts",
    "sneakers":     "sneakers",
    "bags":         "women handbags",
    "earphones":    "earphones",
    "watches":      "wrist watches",
    "bedsheets":    "double bedsheets",
    "kitchenware":  "kitchen utensils",
}


class MeeshoScraper(BaseScraper):
    platform = Platform.MEESHO

    async def scrape_category(self, category: str) -> list[ScrapedProduct]:
        query = CATEGORY_MAP.get(category, category)
        url   = f"{MEESHO_BASE}/search?q={query.replace(' ', '+')}&page=1"

        self._log.info("scraping", platform="meesho", url=url)

        try:
            fetcher = Fetcher(auto_match=True, stealth=True)
            page = fetcher.get(
                url,
                stealthy_headers=True,
                wait_selector="[data-testid='product-card']",
                wait_timeout=15000,
            )
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "too many" in err_str:
                raise ScraperRateLimited("meesho", "rate limited")
            raise ScraperError("meesho", str(e))

        if "robot" in page.html.lower() or "captcha" in page.html.lower():
            raise ScraperRateLimited("meesho", "CAPTCHA detected")

        items = page.css("[data-testid='product-card']")
        if not items:
            # Try fallback selector
            items = page.css(".NewProductCard__CardStyled")
            if not items:
                raise ScraperStructureChanged("meesho", "product card selector not found")

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
        # Title
        title_el = (
            item.css_first("p[class*='ProductTitle']")
            or item.css_first("[data-testid='product-title']")
            or item.css_first("h5")
        )
        title = self.clean_title(title_el.text if title_el else "")
        if not title:
            return None

        # Price — Meesho shows "₹299" format
        price_el = (
            item.css_first("p[class*='DiscountedPrice']")
            or item.css_first("[data-testid='price']")
            or item.css_first("span[class*='price']")
        )
        price = self.extract_price(price_el.text if price_el else "")
        if price <= 0:
            return None

        orig_el  = item.css_first("p[class*='OriginalPrice']")
        orig_price = self.extract_price(orig_el.text if orig_el else "")

        # Image
        img_el = item.css_first("img")
        image_url = img_el.attrib.get("src", "") if img_el else ""

        # Link
        link_el = item.css_first("a")
        href    = link_el.attrib.get("href", "") if link_el else ""
        product_url = self.safe_url(href, MEESHO_BASE)

        # Product ID from URL (e.g. /product-name/123456789)
        parts      = href.rstrip("/").split("/")
        product_id = parts[-1] if parts else href[:32]

        affiliate_url = await build_meesho_affiliate_url(product_url)

        return ScrapedProduct(
            external_id    = product_id or title[:20],
            platform       = Platform.MEESHO,
            title          = title,
            brand          = "",
            image_url      = image_url,
            current_price  = price,
            original_price = orig_price or price,
            affiliate_url  = affiliate_url,
            category       = category,
        )
