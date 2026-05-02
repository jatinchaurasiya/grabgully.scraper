"""
scrapers/flipkart.py
─────────────────────
Flipkart product scraper — Scrapling 0.4.5 + DynamicFetcher.

Flipkart is a React SPA. DynamicFetcher handles JS rendering and
bypasses basic bot-detection. Affiliate links are raw product URLs —
the CueLink Android SDK converts them client-side.
"""
from __future__ import annotations
import asyncio
import re
from scrapling.fetchers.chrome import DynamicFetcher

from core.exceptions import ScraperError, ScraperRateLimited, ScraperStructureChanged
from core.models import Platform, ScrapedProduct
from scrapers.base import BaseScraper, BROWSER_ARGS, BROWSER_VIEWPORT
from integrations.affiliate import build_flipkart_affiliate_url

FLIPKART_BASE = "https://www.flipkart.com"

CATEGORY_MAP: dict[str, str] = {
    "earphones":    "audio/earphones-headphones/pr?sid=0pm%2Ckap&sort=popularity",
    "kurta":        "clothing-and-accessories/topwear/kurtas/pr?sid=clo%2Cash%2C0pr&sort=popularity",
    "sneakers":     "footwear/sports-shoes/pr?sid=osp%2Ca1k&sort=popularity",
    "jeans":        "clothing-and-accessories/bottomwear/jeans/pr?sid=clo%2Cank%2Cbkm&sort=popularity",
}


class FlipkartScraper(BaseScraper):
    platform = Platform.FLIPKART

    async def run(self, categories: list[str]) -> list[ScrapedProduct]:  # type: ignore[override]
        """Wait for RAM to recover after previous scrapers, then delegate to base."""
        await asyncio.sleep(60)
        return await super().run(categories)

    def scrape_category(self, category: str) -> list[ScrapedProduct]:
        slug = CATEGORY_MAP.get(category, f"{category}?sort=popularity")
        url  = f"{FLIPKART_BASE}/{slug}"
        self._log.info("scraping", platform="flipkart", url=url)

        try:
            page = DynamicFetcher.fetch(
                url,
                headless=True,
                wait_selector="div._1AtVbE, div[class*='_1AtVbE'], ._4ddWXP",
                timeout=45000,         # Flipkart renders late
                disable_resources=True,
                network_idle=True,
                extra_args=BROWSER_ARGS,
                viewport=BROWSER_VIEWPORT,
            )
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "too many" in err:
                raise ScraperRateLimited("flipkart", "429 rate limit")
            if "timeout" in err:
                raise ScraperError("flipkart", f"page timeout: {e}")
            raise ScraperError("flipkart", str(e))

        html_lower = str(page).lower()
        if any(kw in html_lower for kw in ("captcha", "access denied", "robot verification")):
            raise ScraperRateLimited("flipkart", "access denied / CAPTCHA")

        # Flipkart product cards have [data-id] attribute
        cards = page.css("[data-id]")
        if not cards:
            cards = page.css("._1AtVbE")
        if not cards:
            cards = page.css("._4ddWXP")
        if not cards:
            raise ScraperStructureChanged(
                "flipkart",
                "[data-id] selector returned nothing — Flipkart layout may have changed",
            )

        products: list[ScrapedProduct] = []
        for card in cards:
            try:
                p = self._parse(card, category)
                if p:
                    products.append(p)
            except Exception as e:
                self._log.debug("parse_skip", reason=str(e))
                continue
        return products

    def _parse(self, card, category: str) -> ScrapedProduct | None:
        # ── Product ID ────────────────────────────────────────────────────────
        product_id = card.attrib.get("data-id", "")
        if not product_id:
            a_el = self.css_first(card, "a[href*='/p/']")
            if a_el:
                href = a_el.attrib.get("href", "")
                m    = re.search(r"/p/([A-Z0-9]+)", href)
                product_id = m.group(1) if m else ""
        if not product_id:
            return None

        # ── Title ─────────────────────────────────────────────────────────────
        title = ""
        for sel in ("._4rR01T", ".s1Q9rs", "a.IRpwTa", "[title]", "a"):
            el = self.css_first(card, sel)
            if el:
                title = el.attrib.get("title", "") or self.clean_title(el.text)
                if title:
                    break
        if not title:
            return None

        # ── Prices ────────────────────────────────────────────────────────────
        price_el   = self.css_first(card, "._30jeq3") or self.css_first(card, "[class*='price']")
        orig_el    = self.css_first(card, "._3I9_wc") or self.css_first(card, "[class*='strike']")
        deal_price = self.extract_price(price_el.text if price_el else "")
        orig_price = self.extract_price(orig_el.text if orig_el else "")
        if deal_price <= 0:
            return None

        disc_el  = self.css_first(card, "._3Ay6Sb") or self.css_first(card, "[class*='discount']")
        discount = self.extract_int(disc_el.text if disc_el else "")
        if discount == 0 and orig_price > deal_price > 0:
            discount = int((1 - deal_price / orig_price) * 100)

        # ── Image ─────────────────────────────────────────────────────────────
        img_el    = self.css_first(card, "img._396cs4") or self.css_first(card, "img")
        image_url = img_el.attrib.get("src", "") if img_el else ""

        # ── URL ───────────────────────────────────────────────────────────────
        link_el = (
            self.css_first(card, "a._1fQZEK")
            or self.css_first(card, "a[href*='/p/']")
            or self.css_first(card, "a")
        )
        href    = link_el.attrib.get("href", "") if link_el else ""
        prod_url = self.safe_url(href, FLIPKART_BASE)
        if not prod_url:
            return None

        return ScrapedProduct(
            external_id    = product_id,
            platform       = Platform.FLIPKART,
            title          = title.strip(),
            brand          = "",
            image_url      = image_url,
            current_price  = deal_price,
            original_price = orig_price or deal_price,
            discount_pct   = discount,
            # Raw URL — CueLink Android SDK converts to affiliate link client-side
            affiliate_url  = build_flipkart_affiliate_url(prod_url),
            category       = category,
        )
