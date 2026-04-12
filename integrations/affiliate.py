"""
integrations/affiliate.py
──────────────────────────
Builds affiliate URLs for every platform.

ARCHITECTURE — CueLink Mobile SDK (Not REST API):
  The Grab Gully Android app uses the CueLink Android SDK (v1.0.3).
  This means affiliate link conversion happens CLIENT-SIDE on the Android app,
  NOT on this backend server.

  The SDK intercepts any URL that appears in an Android TextView and
  automatically converts it into a tracked affiliate link using the registered
  CueLink Channel ID.

  Therefore:
  ─ For Flipkart, Myntra, Meesho, Ajio, Snapdeal → we return the raw, clean
    product URL. The Android SDK handles the affiliate conversion.
  ─ For Amazon → we attach the Associates partner tag directly (Amazon has
    its own affiliate program independent of CueLink).

  The /go/{platform}/{listing_id} endpoint still logs click events to Supabase
  and then redirects to the raw URL. The CueLink SDK intercepts the redirect
  on the Android side.

All Buy buttons in the Android app route through:
  GET /go/{platform}/{listing_id}
which calls log_affiliate_click() then redirects to the URL returned here.

IMPORTANT: Product URLs must NEVER be obfuscated or wrapped server-side.
The CueLink SDK needs the original domain URL to recognize and affiliate them.
"""
from core.config import get_settings


# ─── Amazon ───────────────────────────────────────────────────────────────────

def build_amazon_affiliate_url(asin: str) -> str:
    """
    Build an Amazon India affiliate URL from an ASIN.
    Amazon uses its own Associates program — CueLink SDK does NOT handle Amazon.
    Format: https://www.amazon.in/dp/{ASIN}?tag={partner_tag}&linkCode=ogi
    """
    tag = get_settings().amazon_partner_tag
    return f"https://www.amazon.in/dp/{asin}?tag={tag}&linkCode=ogi&th=1&psc=1"


# ─── Platforms handled by CueLink Android SDK ────────────────────────────────
# These functions return the clean raw product URL.
# The CueLink SDK (Channel ID configured in AndroidManifest.xml) will
# intercept clicks on these URLs inside the Android app and auto-convert
# them into tracked affiliate URLs client-side.

def build_flipkart_affiliate_url(product_url: str) -> str:
    """
    Return raw Flipkart product URL.
    CueLink Android SDK auto-affiliates this on the client side.
    """
    return product_url or ""


def build_myntra_affiliate_url(product_url: str) -> str:
    """
    Return raw Myntra product URL.
    CueLink Android SDK auto-affiliates this on the client side.
    """
    return product_url or ""


def build_meesho_affiliate_url(product_url: str) -> str:
    """
    Return raw Meesho product URL.
    CueLink Android SDK auto-affiliates this on the client side.
    """
    return product_url or ""


def build_ajio_affiliate_url(product_url: str) -> str:
    """
    Return raw Ajio product URL.
    CueLink Android SDK auto-affiliates this on the client side.
    """
    return product_url or ""


def build_snapdeal_affiliate_url(product_url: str) -> str:
    """
    Return raw Snapdeal product URL.
    CueLink Android SDK auto-affiliates this on the client side.
    """
    return product_url or ""


# ─── Dispatcher ───────────────────────────────────────────────────────────────

def build_affiliate_url(
    platform: str,
    product_url: str,
    asin: str = "",
) -> str:
    """
    Dispatcher — given a platform name, build the correct URL for storage.

    Amazon → direct Associates deep link (server-side).
    All others → raw product URL (CueLink Android SDK handles affiliate
    conversion on the client side).

    Args:
        platform:    Platform name string (e.g. "flipkart", "amazon").
        product_url: Raw scraped product URL.
        asin:        Amazon ASIN (only required when platform == "amazon").

    Returns:
        URL to store in DB and serve via the API.
    """
    platform = platform.lower()

    if platform == "amazon":
        return build_amazon_affiliate_url(asin) if asin else product_url

    # All other platforms: return raw URL — CueLink SDK affiliates client-side
    builders = {
        "flipkart": build_flipkart_affiliate_url,
        "myntra":   build_myntra_affiliate_url,
        "meesho":   build_meesho_affiliate_url,
        "ajio":     build_ajio_affiliate_url,
        "snapdeal": build_snapdeal_affiliate_url,
    }
    builder = builders.get(platform)
    return builder(product_url) if builder else product_url
