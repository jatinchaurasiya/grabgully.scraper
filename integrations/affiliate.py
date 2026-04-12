"""
integrations/affiliate.py
──────────────────────────
Builds affiliate URLs for every platform.
All Buy buttons in the Android app route through:
  GET /go/{platform}/{listing_id}
which calls log_affiliate_click() then redirects to the URL built here.

IMPORTANT: These URLs contain your affiliate tracking parameters.
Never expose them in the Android APK — only on the backend.
"""
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs, urljoin
from core.config import get_settings


def _settings():
    return get_settings()


def build_amazon_affiliate_url(asin: str) -> str:
    """
    Build a standard Amazon India affiliate URL from an ASIN.
    Format: https://www.amazon.in/dp/{ASIN}?tag={affiliate_tag}
    """
    tag = _settings().amazon_partner_tag
    return f"https://www.amazon.in/dp/{asin}?tag={tag}&linkCode=ogi&th=1&psc=1"


def build_flipkart_affiliate_url(product_url: str) -> str:
    """
    Append Flipkart affiliate tracking ID to a product URL.
    Format: original_url&affid={affiliate_id}
    """
    s = _settings()
    if not s.flipkart_affiliate_id:
        return product_url
    sep = "&" if "?" in product_url else "?"
    return f"{product_url}{sep}affid={s.flipkart_affiliate_id}"


def build_myntra_affiliate_url(product_url: str) -> str:
    """
    Myntra affiliate via vCommission — append tracking params.
    Sign up at vcommission.com to get your tracking URL template.
    """
    if not product_url:
        return product_url
    # vCommission deep link template (replace TRACKING_ID with yours)
    # Format: https://track.vcommission.com/click?offer_id=XXX&aff_id=YOUR_ID&url=ENCODED_URL
    from urllib.parse import quote
    aff_id = "MYNTRA_AFF_ID"  # Replace with your vCommission affiliate ID
    encoded = quote(product_url, safe="")
    return f"https://track.vcommission.com/click?offer_id=6440&aff_id={aff_id}&url={encoded}"


def build_meesho_affiliate_url(product_url: str) -> str:
    """
    Meesho affiliate — append tracking parameter.
    """
    if not product_url:
        return product_url
    sep = "&" if "?" in product_url else "?"
    return f"{product_url}{sep}utm_source=grabgully&utm_medium=affiliate"


def build_ajio_affiliate_url(product_url: str) -> str:
    """
    Ajio affiliate via CJ Affiliate — deep link template.
    Sign up at cj.com to get your advertiser link template.
    """
    from urllib.parse import quote
    if not product_url:
        return product_url
    cj_pid = "CJ_PID"          # Replace with your CJ publisher ID
    cj_aid = "AJIO_ADVERTISER" # Replace with Ajio advertiser ID on CJ
    encoded = quote(product_url, safe="")
    return f"https://www.anrdoezrs.net/click-{cj_pid}-{cj_aid}?url={encoded}"


def build_snapdeal_affiliate_url(product_url: str) -> str:
    """
    Snapdeal affiliate — direct affiliate URL format.
    """
    if not product_url:
        return product_url
    sep = "&" if "?" in product_url else "?"
    return f"{product_url}{sep}utm_source=grabgully&utm_medium=cpa&utm_campaign=deals"


def build_nykaa_affiliate_url(product_url: str) -> str:
    """
    Nykaa affiliate — append tracking params.
    """
    if not product_url:
        return product_url
    sep = "&" if "?" in product_url else "?"
    return f"{product_url}{sep}utm_source=grabgully&utm_medium=affiliate"


def build_affiliate_url(platform: str, product_url: str, asin: str = "") -> str:
    """
    Dispatcher — given a platform name, build the correct affiliate URL.
    Used by the /go endpoint.
    """
    builders = {
        "amazon":   lambda: build_amazon_affiliate_url(asin) if asin else product_url,
        "flipkart": lambda: build_flipkart_affiliate_url(product_url),
        "myntra":   lambda: build_myntra_affiliate_url(product_url),
        "meesho":   lambda: build_meesho_affiliate_url(product_url),
        "ajio":     lambda: build_ajio_affiliate_url(product_url),
        "snapdeal": lambda: build_snapdeal_affiliate_url(product_url),
        "nykaa":    lambda: build_nykaa_affiliate_url(product_url),
    }
    builder = builders.get(platform.lower())
    return builder() if builder else product_url
