"""
scrapers/ajio.py
────────────────
Ajio scraper — Reliance's fashion platform.
Uses Scrapling + Playwright for JS-rendered pages.
Affiliate links are generated via CueLink (build_ajio_affiliate_url).
"""
from __future__ import annotations
from scrapling.auto import Fetcher
from core.exceptions import ScraperError, ScraperRateLimited, ScraperStructureChanged
from core.models import Platform, ScrapedProduct
from scrapers.base import BaseScraper
from integrations.affiliate import build_ajio_affiliate_url

AJIO_BASE = "https://www.ajio.com"

CATEGORY_MAP = {
    "kurta":    "men-kurta-and-kurta-sets",
    "tshirts":  "men-tshirts",
    "jeans":    "men-jeans",
    "sneakers": "men-sport-shoes",
    "saree":    "women-sarees",
    "dresses":  "women-dresses",
    "tops":     "women-tops",
    "bags":     "women-handbags",
    "watches":  "men-watches",
}


class AjioScraper(BaseScraper):
    platform = Platform.AJIO

    async def scrape_category(self, category: str) -> list[ScrapedProduct]:
        slug = CATEGORY_MAP.get(category, category)
        url  = f"{AJIO_BASE}/s/{slug}?rows=40&start=0&sortBy=newn"

        self._log.info("scraping", platform="ajio", url=url)

        try:
            fetcher = Fetcher(auto_match=True, stealth=True)
            page = fetcher.get(
                url,
                stealthy_headers=True,
                wait_selector=".item",
                wait_timeout=20000,
            )
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str:
                raise ScraperRateLimited("ajio", "rate limited")
            raise ScraperError("ajio", str(e))

        if "access denied" in page.html.lower():
            raise ScraperRateLimited("ajio", "access denied")

        items = page.css(".item")
        if not items:
            items = page.css("[class*='item-info']")
            if not items:
                raise ScraperStructureChanged("ajio", ".item selector returned nothing")

        products = []
        for item in items:
            try:
                p = self._parse_product(item, category)
                if p:
                    products.append(p)
            except Exception:
                continue

        return products

    def _parse_product(self, item, category: str) -> ScrapedProduct | None:
        brand_el  = item.css_first(".brand")
        title_el  = item.css_first(".nameCls") or item.css_first("h2")
        brand     = self.clean_title(brand_el.text if brand_el else "")
        title_str = self.clean_title(title_el.text if title_el else "")
        full      = f"{brand} {title_str}".strip()
        if not full:
            return None

        # Price
        sale_el  = item.css_first(".price .sale") or item.css_first(".price strong")
        orig_el  = item.css_first(".price .orginal-price")
        sale_price = self.extract_price(sale_el.text if sale_el else "")
        orig_price = self.extract_price(orig_el.text if orig_el else "")
        if sale_price <= 0:
            return None

        disc_el  = item.css_first(".discount")
        discount = self.extract_int(disc_el.text if disc_el else "")

        img_el   = item.css_first("img")
        image_url = img_el.attrib.get("src", "") if img_el else ""

        link_el  = item.css_first("a")
        href     = link_el.attrib.get("href", "") if link_el else ""
        url      = self.safe_url(href, AJIO_BASE)

        # Ajio product IDs are in the URL slug (last segment before query)
        slug_part  = href.rstrip("/").split("/")[-1].split("?")[0]
        product_id = slug_part or (brand + title_str)[:32]

        return ScrapedProduct(
            external_id    = product_id,
            platform       = Platform.AJIO,
            title          = full,
            brand          = brand,
            image_url      = image_url,
            current_price  = sale_price,
            original_price = orig_price or sale_price,
            discount_pct   = discount,
            affiliate_url  = build_ajio_affiliate_url(url),  # raw URL — CueLink SDK affiliates
            category       = category,
        )
