"""
scrapers/ajio.py
────────────────
Ajio scraper — Scrapling 0.4.5 + DynamicFetcher.
"""
from __future__ import annotations
from scrapling.fetchers.chrome import DynamicFetcher

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

    def scrape_category(self, category: str) -> list[ScrapedProduct]:
        slug = CATEGORY_MAP.get(category, category)
        url  = f"{AJIO_BASE}/s/{slug}?rows=40&start=0&sortBy=newn"
        self._log.info("scraping", platform="ajio", url=url)

        try:
            page = DynamicFetcher.fetch(
                url,
                headless=True,
                wait_selector=".item",
                timeout=40000,
                disable_resources=True,
                network_idle=True,
                extra_headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept-Language": "en-IN,en;q=0.9",
                    "Referer": "https://www.google.com/",
                },
            )
        except Exception as e:
            err = str(e).lower()
            if "429" in err:
                raise ScraperRateLimited("ajio", "rate limited")
            raise ScraperError("ajio", str(e))

        if "access denied" in (page.html or "").lower():
            raise ScraperRateLimited("ajio", "access denied")

        items = page.css(".item")
        if not items:
            items = page.css("[class*='item-info']")
            if not items:
                raise ScraperStructureChanged("ajio", ".item selector returned nothing")

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
        brand_el  = self.css_first(item, ".brand")
        title_el  = self.css_first(item, ".nameCls") or self.css_first(item, "h2")
        brand     = self.clean_title(brand_el.text if brand_el else "")
        title_str = self.clean_title(title_el.text if title_el else "")
        full      = f"{brand} {title_str}".strip()
        if not full:
            return None

        sale_el    = self.css_first(item, ".price .sale") or self.css_first(item, ".price strong")
        orig_el    = self.css_first(item, ".price .orginal-price")
        sale_price = self.extract_price(sale_el.text if sale_el else "")
        orig_price = self.extract_price(orig_el.text if orig_el else "")
        if sale_price <= 0:
            return None

        disc_el  = self.css_first(item, ".discount")
        discount = self.extract_int(disc_el.text if disc_el else "")

        img_el    = self.css_first(item, "img")
        image_url = img_el.attrib.get("src", "") if img_el else ""

        link_el   = self.css_first(item, "a")
        href      = link_el.attrib.get("href", "") if link_el else ""
        prod_url  = self.safe_url(href, AJIO_BASE)

        slug_part = href.rstrip("/").split("/")[-1].split("?")[0]
        prod_id   = slug_part or (brand + title_str)[:32]

        return ScrapedProduct(
            external_id    = prod_id,
            platform       = Platform.AJIO,
            title          = full,
            brand          = brand,
            image_url      = image_url,
            current_price  = sale_price,
            original_price = orig_price or sale_price,
            discount_pct   = discount,
            affiliate_url  = build_ajio_affiliate_url(prod_url),
            category       = category,
        )
