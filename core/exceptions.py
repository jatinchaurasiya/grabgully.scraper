"""
core/exceptions.py
──────────────────
All custom exceptions. Never raise generic Exception — always be specific.
"""


class GrabGullyError(Exception):
    """Base exception for all Grab Gully errors."""


class ScraperError(GrabGullyError):
    """Raised when a scraper fails to fetch or parse a page."""
    def __init__(self, platform: str, reason: str):
        self.platform = platform
        self.reason = reason
        super().__init__(f"[{platform}] Scraper failed: {reason}")


class ScraperRateLimited(ScraperError):
    """Raised when a platform returns 429 or CAPTCHA."""


class ScraperStructureChanged(ScraperError):
    """Raised when CSS selectors find nothing — site structure changed."""


class DatabaseError(GrabGullyError):
    """Raised on Supabase write/read failures."""


class CacheError(GrabGullyError):
    """Raised on Redis failures — non-fatal, app should continue."""


class AffiliateAPIError(GrabGullyError):
    """Raised when Amazon PA-API or Flipkart API returns an error."""


class NotificationError(GrabGullyError):
    """Raised when FCM push notification fails to send."""


class AuthError(GrabGullyError):
    """Raised when internal API auth fails."""
    status_code: int = 403
