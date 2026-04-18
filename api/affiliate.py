"""
api/affiliate.py
─────────────────
The /go/{platform}/{listing_id} redirect endpoint.
Every Buy button in the Android app hits this — NEVER exposes raw affiliate URLs.

Flow:
  1. Validate listing exists in DB
  2. Rate-limit: 1 click per (user_ip_hash + listing) per 5 minutes
  3. Log click to affiliate_clicks table
  4. Award XP to user if authenticated
  5. 302 redirect to affiliate URL

This protects affiliate links from scraping and gives us an audit log
for commission disputes with platforms.
"""
from __future__ import annotations
import hashlib
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Header
from fastapi.responses import RedirectResponse

from core.logger import get_logger
from services.db import get_db, log_affiliate_click, award_xp
from services.cache import get_cache

router = APIRouter(tags=["affiliate"])
log    = get_logger("api.affiliate")

RATE_LIMIT_WINDOW = 300   # 5 minutes
RATE_LIMIT_MAX    = 1     # 1 click per product per window per user


@router.get("/{platform}/{listing_id}")
async def affiliate_redirect(
    platform:       str,
    listing_id:     str,
    request:        Request,
    authorization:  Optional[str] = Header(None),
):
    """
    Affiliate link redirect.
    Android app calls: GET /go/amazon/listing-uuid
    We log the click, then redirect to the real affiliate URL.
    """
    db    = get_db()
    cache = get_cache()

    # 1. Fetch listing
    try:
        res = (
            db.table("platform_listings")
            .select("id, platform, affiliate_url, title, current_price")
            .eq("id", listing_id)
            .eq("platform", platform.lower())
            .single()
            .execute()
        )
        if not res.data:
            raise HTTPException(404, "Listing not found")
        listing = res.data
    except HTTPException:
        raise
    except Exception as e:
        log.error("affiliate_lookup_failed", listing_id=listing_id, error=str(e))
        raise HTTPException(500, "Failed to resolve affiliate link")

    # 2. Rate limiting — prevent link farming
    client_ip   = _get_client_ip(request)
    ip_hash     = hashlib.sha256(client_ip.encode()).hexdigest()[:16]
    rate_key    = f"aff_rl:{ip_hash}:{listing_id}"
    click_count = await cache.incr(rate_key, ttl=RATE_LIMIT_WINDOW)

    if click_count > RATE_LIMIT_MAX:
        log.warning("affiliate_rate_limited",
                    listing_id=listing_id, ip_hash=ip_hash, count=click_count)
        # Still redirect — just don't log as qualifying click
        return RedirectResponse(url=listing["affiliate_url"], status_code=302)

    # 3. Resolve user from JWT (optional — anonymous clicks are fine)
    user_id = await _extract_user_id(authorization)

    # 4. Log the click (fire-and-forget — don't let logging failure block redirect)
    try:
        await log_affiliate_click(
            user_id    = user_id,
            listing_id = listing_id,
            platform   = platform.lower(),
            ip_hash    = ip_hash,
        )
    except Exception as e:
        log.warning("affiliate_click_log_error", error=str(e))

    # 5. Award XP (non-blocking)
    if user_id:
        try:
            await award_xp(
                user_id=user_id,
                action_type="affiliate_click",
                xp=5,
                metadata={"listing_id": listing_id},
            )
        except Exception:
            pass   # XP failure must never block the redirect

    log.info("affiliate_redirect",
             platform=platform, listing_id=listing_id,
             user_id=user_id or "anonymous",
             price=listing.get("current_price"))

    # 6. Redirect
    return RedirectResponse(url=listing["affiliate_url"], status_code=302)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_client_ip(request: Request) -> str:
    """Extract real IP respecting Railway's X-Forwarded-For header."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _extract_user_id(authorization: Optional[str]) -> Optional[str]:
    """
    Validate Supabase JWT and return user_id.
    Verifies HS256 signature using SUPABASE_JWT_SECRET.
    Returns None for unauthenticated requests (anonymous users).
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    try:
        from jose import jwt, JWTError
        from core.config import get_settings
        s = get_settings()
        payload = jwt.decode(
            token,
            s.supabase_jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},  # Supabase uses aud="authenticated"
        )
        return payload.get("sub")   # Supabase user UUID
    except Exception:
        return None


