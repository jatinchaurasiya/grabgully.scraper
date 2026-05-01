"""
scrapers/meesho.py
──────────────────
Meesho scraper — Scrapling 0.4.5 + DynamicFetcher.
Meesho is React-based; headless browser needed.
"""
from __future__ import annotations
from scrapling.fetchers.chrome import DynamicFetcher

from core.exceptions import ScraperError, ScraperRateLimited, ScraperStructureChanged
from core.models import Platform, ScrapedProduct
from scrapers.base import BaseScraper
from integrations.affiliate import build_meesho_affiliate_url

MEESHO_BASE = "https://meesho.com"

CATEGORY_MAP = {
    "kurta":       "kurta for women",
    "saree":       "sarees",
    "jeans":       "women jeans",
    "tshirts":     "men tshirts",
    "sneakers":    "sneakers",
    "bags":        "women handbags",
    "earphones":   "earphones",
    "watches":     "wrist watches",
    "bedsheets":   "double bedsheets",
    "kitchen":     "kitchen utensils",
}


class MeeshoScraper(BaseScraper):
    platform = Platform.MEESHO

    def scrape_category(self, category: str) -> list[ScrapedProduct]:
        query = CATEGORY_MAP.get(category, category)
        url   = f"{MEESHO_BASE}/search?q={query.replace(' ', '+')}&page=1"
        self._log.info("scraping", platform="meesho", url=url)

        try:
            page = DynamicFetcher.fetch(
                url,
                headless=True,
                wait_selector="[class*='ProductCard'], [data-testid='product-card'], [class*='product-card']",
                timeout=45000,
                disable_resources=True,
                network_idle=True,
            )
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "too many" in err:
                raise ScraperRateLimited("meesho", "rate limited")
            raise ScraperError("meesho", str(e))

        html_lower = str(page).lower()
        if "robot" in html_lower or "captcha" in html_lower:
            raise ScraperRateLimited("meesho", "CAPTCHA detected")

        items = (
            page.css("[class*='ProductCard']")
            or page.css("[data-testid='product-card']")
            or page.css("[class*='NewProductCard']")
        )
        if not items:
            raise ScraperStructureChanged("meesho", "product card selector not found")

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
            self.css_first(item, "p[class*='ProductTitle']")
            or self.css_first(item, "[data-testid='product-title']")
            or self.css_first(item, "h5")
        )
        title = self.clean_title(title_el.text if title_el else "")
        if not title:
            return None

        price_el = (
            self.css_first(item, "p[class*='DiscountedPrice']")
            or self.css_first(item, "[data-testid='price']")
            or self.css_first(item, "span[class*='price']")
        )
        price = self.extract_price(price_el.text if price_el else "")
        if price <= 0:
            return None

        orig_el    = self.css_first(item, "p[class*='OriginalPrice']")
        orig_price = self.extract_price(orig_el.text if orig_el else "")

        img_el    = self.css_first(item, "img")
        image_url = img_el.attrib.get("src", "") if img_el else ""

        link_el   = self.css_first(item, "a")
        href      = link_el.attrib.get("href", "") if link_el else ""
        prod_url  = self.safe_url(href, MEESHO_BASE)

        # Extract the stable numeric product ID from the URL.
        # Meesho URLs: /product-name-slug/123456789
        # The slug changes on promotions; the trailing numeric ID is stable.
        import re
        numeric_ids = re.findall(r'\d{6,}', href)   # 6+ digit numbers in the URL
        prod_id     = numeric_ids[-1] if numeric_ids else ""
        if not prod_id:
            prod_id = title[:24].replace(" ", "_")

        return ScrapedProduct(
            external_id    = prod_id,
            platform       = Platform.MEESHO,
            title          = title,
            brand          = "",
            image_url      = image_url,
            current_price  = price,
            original_price = orig_price or price,
            affiliate_url  = build_meesho_affiliate_url(prod_url),
            category       = category,
        )
