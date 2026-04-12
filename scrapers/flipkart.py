"""
scrapers/flipkart.py
─────────────────────
Flipkart product scraper using Scrapling + Playwright.

Flipkart is a JS-heavy SPA — we use a headless browser via Scrapling's
Fetcher to render the page before parsing.

Note: This is distinct from integrations/flipkart.py which was the old
Flipkart Affiliate API integration. This scraper hits the public website
and converts all links to CueLink affiliate URLs.

Category URL pattern:
  https://www.flipkart.com/{category}?sort=popularity&page=1
"""
from __future__ import annotations
import asyncio
import re

from scrapling.auto import Fetcher

from core.exceptions import ScraperError, ScraperRateLimited, ScraperStructureChanged
from core.models import Platform, ScrapedProduct
from scrapers.base import BaseScraper
from integrations.affiliate import build_flipkart_affiliate_url

FLIPKART_BASE = "https://www.flipkart.com"

# Map internal category names → Flipkart URL slugs
CATEGORY_MAP: dict[str, str] = {
    "smartphones":  "mobiles/pr?sid=tyy%2C4io&sort=popularity",
    "laptops":      "computers/laptops/pr?sid=6bo%2Cb5g&sort=popularity",
    "earphones":    "audio/earphones-headphones/pr?sid=0pm%2Ckap&sort=popularity",
    "headphones":   "audio/headphones/pr?sid=0pm%2Ckap&sort=popularity",
    "smartwatches": "wearable-smart-devices/smartwatches/pr?sid=ajy%2Cbycu&sort=popularity",
    "televisions":  "televisions/pr?sid=ckf%2Cczl&sort=popularity",
    "tablets":      "tablets/pr?sid=tyy%2Cezb&sort=popularity",
    "tshirts":      "clothing-and-accessories/topwear/tshirts/pr?sid=clo%2Cash%2Cahz&sort=popularity",
    "jeans":        "clothing-and-accessories/bottomwear/jeans/pr?sid=clo%2Cank%2Cbkm&sort=popularity",
    "sneakers":     "footwear/sports-shoes/pr?sid=osp%2Ca1k&sort=popularity",
    "bags":         "bags-wallets-luggage/bags/pr?sid=reh%2Cdjn&sort=popularity",
    "skincare":     "beauty/skincare/pr?sid=sl0%2C01k&sort=popularity",
    "kitchen":      "home-kitchen/kitchen-appliances/pr?sid=j9e%2Caj5%2C7hc&sort=popularity",
    "cameras":      "cameras/digital-cameras/pr?sid=cameras%2Cjl0&sort=popularity",
}


class FlipkartScraper(BaseScraper):
    """
    Scrapes Flipkart product listing pages using Scrapling + Playwright.

    Each product card on Flipkart listing pages uses CSS classes that are
    obfuscated but stable enough for pattern-matching. We match on data
    attributes and structure rather than brittle class names.
    """

    platform = Platform.FLIPKART

    async def scrape_category(self, category: str) -> list[ScrapedProduct]:
        slug = CATEGORY_MAP.get(category, f"{category}?sort=popularity")
        url  = f"{FLIPKART_BASE}/{slug}"

        self._log.info("scraping", platform="flipkart", url=url)

        try:
            fetcher = Fetcher(auto_match=True, stealth=True)
            page = fetcher.get(
                url,
                stealthy_headers=True,
                # Wait for at least one product card to appear
                wait_selector="[data-id]",
                wait_timeout=20_000,    # 20 s — Flipkart can be slow
            )
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "too many" in err:
                raise ScraperRateLimited("flipkart", "429 rate limit")
            if "timeout" in err:
                raise ScraperError("flipkart", f"page timeout: {e}")
            raise ScraperError("flipkart", str(e))

        # Guard: captcha / geo-block / maintenance page
        html_lower = page.html.lower()
        if any(kw in html_lower for kw in ("captcha", "access denied", "robot verification")):
            raise ScraperRateLimited("flipkart", "access denied / CAPTCHA")

        # Product cards  — Flipkart uses [data-id] on each product tile
        cards = page.css("[data-id]")
        if not cards:
            # Fallback: match by the "Add to Cart" / price element presence
            cards = page.css("div._1AtVbE")
            if not cards:
                raise ScraperStructureChanged(
                    "flipkart",
                    "[data-id] selector returned nothing — site layout may have changed",
                )

        products: list[ScrapedProduct] = []
        for card in cards:
            try:
                # CueLink URL generation is async — await per card
                product = await self._parse_product(card, category)
                if product:
                    products.append(product)
            except Exception as e:
                self._log.debug("parse_skip", reason=str(e))
                continue

        self._log.info(
            "scraped",
            platform="flipkart",
            category=category,
            count=len(products),
        )
        return products

    async def _parse_product(self, card, category: str) -> ScrapedProduct | None:
        # ── Product ID ─────────────────────────────────────────────────────
        product_id = card.attrib.get("data-id", "")
        if not product_id:
            # Try extracting from the product anchor href
            a_el = card.css_first("a[href*='/p/']")
            if a_el:
                href       = a_el.attrib.get("href", "")
                pid_match  = re.search(r"/p/([A-Z0-9]+)", href)
                product_id = pid_match.group(1) if pid_match else ""
        if not product_id:
            return None

        # ── Title ──────────────────────────────────────────────────────────
        # Flipkart uses several obfuscated classes; try in priority order.
        title = ""
        for sel in ("._4rR01T", ".s1Q9rs", "a.IRpwTa", "[title]", "a"):
            el = card.css_first(sel)
            if el:
                title = el.attrib.get("title", "") or self.clean_title(el.text)
                if title:
                    break
        if not title:
            return None

        # ── Price ──────────────────────────────────────────────────────────
        price_el = card.css_first("._30jeq3") or card.css_first("[class*='price']")
        orig_el  = card.css_first("._3I9_wc") or card.css_first("[class*='strike']")
        deal_price = self.extract_price(price_el.text if price_el else "")
        orig_price = self.extract_price(orig_el.text if orig_el else "")
        if deal_price <= 0:
            return None

        # ── Discount ───────────────────────────────────────────────────────
        disc_el  = card.css_first("._3Ay6Sb") or card.css_first("[class*='discount']")
        discount = self.extract_int(disc_el.text if disc_el else "")
        if discount == 0 and orig_price > deal_price > 0:
            discount = int((1 - deal_price / orig_price) * 100)

        # ── Image ──────────────────────────────────────────────────────────
        img_el    = card.css_first("img._396cs4") or card.css_first("img")
        image_url = img_el.attrib.get("src", "") if img_el else ""

        # ── Product URL ────────────────────────────────────────────────────
        link_el = card.css_first("a._1fQZEK") or card.css_first("a[href*='/p/']") or card.css_first("a")
        href    = link_el.attrib.get("href", "") if link_el else ""
        product_url = self.safe_url(href, FLIPKART_BASE)
        if not product_url:
            return None

        # ── CueLink Affiliate URL ──────────────────────────────────────────
        affiliate_url = await build_flipkart_affiliate_url(product_url)

        return ScrapedProduct(
            external_id    = product_id,
            platform       = Platform.FLIPKART,
            title          = title.strip(),
            brand          = "",              # Brand not reliably in listing cards
            image_url      = image_url,
            current_price  = deal_price,
            original_price = orig_price or deal_price,
            discount_pct   = discount,
            affiliate_url  = affiliate_url,
            category       = category,
        )
