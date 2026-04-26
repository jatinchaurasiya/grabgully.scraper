"""
scrapers/flipkart.py
─────────────────────
Flipkart product scraper — Scrapling 0.4.5 + DynamicFetcher.

Flipkart is a React SPA. DynamicFetcher handles JS rendering and
bypasses basic bot-detection. Affiliate links are raw product URLs —
the CueLink Android SDK converts them client-side.
"""
from __future__ import annotations
import re
from scrapling.fetchers.chrome import DynamicFetcher

from core.exceptions import ScraperError, ScraperRateLimited, ScraperStructureChanged
from core.models import Platform, ScrapedProduct
from scrapers.base import BaseScraper
from integrations.affiliate import build_flipkart_affiliate_url

FLIPKART_BASE = "https://www.flipkart.com"

CATEGORY_MAP: dict[str, str] = {
    "smartphones":  "mobiles/pr?sid=tyy%2C4io&sort=popularity",
    "laptops":      "computers/laptops/pr?sid=6bo%2Cb5g&sort=popularity",
    "earphones":    "audio/earphones-headphones/pr?sid=0pm%2Ckap&sort=popularity",
    "headphones":   "audio/headphones/pr?sid=0pm%2Ckap&sort=popularity",
    "smartwatches": "wearable-smart-devices/smartwatches/pr?sid=ajy%2Cbycu&sort=popularity",
    "televisions":  "televisions/pr?sid=ckf%2Cczl&sort=popularity",
    "tshirts":      "clothing-and-accessories/topwear/tshirts/pr?sid=clo%2Cash%2Cahz&sort=popularity",
    "jeans":        "clothing-and-accessories/bottomwear/jeans/pr?sid=clo%2Cank%2Cbkm&sort=popularity",
    "sneakers":     "footwear/sports-shoes/pr?sid=osp%2Ca1k&sort=popularity",
    "bags":         "bags-wallets-luggage/bags/pr?sid=reh%2Cdjn&sort=popularity",
    "skincare":     "beauty/skincare/pr?sid=sl0%2C01k&sort=popularity",
    "watches":      "jewellery-and-watches/watches/pr?sid=mcn%2C6zu&sort=popularity",
}


class FlipkartScraper(BaseScraper):
    platform = Platform.FLIPKART

    def scrape_category(self, category: str) -> list[ScrapedProduct]:
        slug = CATEGORY_MAP.get(category, f"{category}?sort=popularity")
        url  = f"{FLIPKART_BASE}/{slug}"
        self._log.info("scraping", platform="flipkart", url=url)

        try:
            page = DynamicFetcher.fetch(
                url,
                headless=True,
                wait_selector="[data-id]",
                timeout=25000,         # Flipkart can be slow
                disable_resources=True,
            )
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "too many" in err:
                raise ScraperRateLimited("flipkart", "429 rate limit")
            if "timeout" in err:
                raise ScraperError("flipkart", f"page timeout: {e}")
            raise ScraperError("flipkart", str(e))

        html_lower = page.html.lower() if page.html else ""
        if any(kw in html_lower for kw in ("captcha", "access denied", "robot verification")):
            raise ScraperRateLimited("flipkart", "access denied / CAPTCHA")

        # Flipkart product cards have [data-id] attribute
        cards = page.css("[data-id]")
        if not cards:
            cards = page.css("div._1AtVbE")
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
