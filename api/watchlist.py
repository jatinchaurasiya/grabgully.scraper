"""
api/watchlist.py
─────────────────
Watchlist management — add/remove/list items, set target price alerts.
All operations require authentication (Supabase JWT).
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel

from core.logger import get_logger
from services.db import get_db, award_xp

router = APIRouter(tags=["watchlist"])
log    = get_logger("api.watchlist")


# ── Auth dependency ───────────────────────────────────────────────────────────

async def require_user(authorization: Optional[str] = Header(None)) -> str:
    """
    Extract and validate user_id from Supabase JWT.
    Verifies HS256 signature using SUPABASE_JWT_SECRET.
    Raises 401 if token is missing, invalid, or forged.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        from jose import jwt
        from core.config import get_settings
        s = get_settings()
        payload = jwt.decode(
            token,
            s.supabase_jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},  # Supabase uses aud="authenticated"
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(401, "Invalid token")
        return user_id
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Invalid token")


# ── Request / Response models ─────────────────────────────────────────────────

class AddToWatchlistRequest(BaseModel):
    listing_id:   str
    target_price: Optional[float] = None


class WatchlistItem(BaseModel):
    id:            str
    listing_id:    str
    target_price:  Optional[float]
    is_notified:   bool
    created_at:    str
    title:         str
    platform:      str
    current_price: float
    image_url:     str
    affiliate_url: str


class SetAlertRequest(BaseModel):
    target_price: float


class UpdateFcmTokenRequest(BaseModel):
    fcm_token: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[WatchlistItem])
async def get_watchlist(user_id: str = Depends(require_user)):
    """Fetch all watchlist items for the authenticated user."""
    db = get_db()
    try:
        res = (
            db.table("watchlist")
            .select(
                "id, listing_id, target_price, is_notified, created_at, "
                "platform_listings(title, platform, current_price, image_url, affiliate_url)"
            )
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        items = []
        for row in (res.data or []):
            listing = row.get("platform_listings") or {}
            items.append(WatchlistItem(
                id            = row["id"],
                listing_id    = row["listing_id"],
                target_price  = row.get("target_price"),
                is_notified   = row.get("is_notified", False),
                created_at    = str(row.get("created_at", "")),
                title         = listing.get("title", ""),
                platform      = listing.get("platform", ""),
                current_price = listing.get("current_price", 0.0),
                image_url     = listing.get("image_url", ""),
                affiliate_url = listing.get("affiliate_url", ""),
            ))
        return items
    except Exception as e:
        log.error("get_watchlist_failed", user_id=user_id, error=str(e))
        raise HTTPException(500, "Failed to fetch watchlist")


@router.post("", status_code=201)
async def add_to_watchlist(
    body:    AddToWatchlistRequest,
    user_id: str = Depends(require_user),
):
    """Add a product to watchlist. Optionally set a target price for alerts."""
    db = get_db()

    # Check duplicate
    existing = (
        db.table("watchlist")
        .select("id")
        .eq("user_id", user_id)
        .eq("listing_id", body.listing_id)
        .execute()
    )
    if existing.data:
        raise HTTPException(409, "Already in watchlist")

    # Max 50 items on free tier
    count_res = (
        db.table("watchlist")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .execute()
    )
    if (count_res.count or 0) >= 50:
        raise HTTPException(403, "Watchlist limit reached. You can track up to 50 products.")

    try:
        row = {
            "user_id":      user_id,
            "listing_id":   body.listing_id,
            "target_price": body.target_price,
            "is_notified":  False,
        }
        db.table("watchlist").insert(row).execute()

        # Award XP for adding to watchlist
        await award_xp(user_id=user_id, action_type="watchlist_add", xp=5)

        return {"message": "Watchlist mein add ho gaya!"}
    except Exception as e:
        log.error("add_watchlist_failed", user_id=user_id, error=str(e))
        raise HTTPException(500, "Failed to add to watchlist")


@router.delete("/{item_id}")
async def remove_from_watchlist(item_id: str, user_id: str = Depends(require_user)):
    """Remove a product from the user's watchlist. Returns 404 if not found."""
    db = get_db()
    try:
        res = (
            db.table("watchlist")
            .delete()
            .eq("id", item_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not res.data:
            raise HTTPException(404, "Item not found in your watchlist")
        return {"message": "Watchlist se hata diya"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Failed to remove from watchlist")


@router.patch("/{item_id}/alert")
async def set_price_alert(
    item_id: str,
    body:    SetAlertRequest,
    user_id: str = Depends(require_user),
):
    """Set or update target price alert for a watchlist item."""
    db = get_db()
    try:
        db.table("watchlist") \
          .update({"target_price": body.target_price, "is_notified": False}) \
          .eq("id", item_id) \
          .eq("user_id", user_id) \
          .execute()
        return {"message": f"Alert set: Rs {body.target_price:,.0f} pe batayenge!"}
    except Exception as e:
        raise HTTPException(500, "Failed to set alert")


@router.patch("/fcm-token", status_code=200)
async def update_fcm_token(
    body:    UpdateFcmTokenRequest,
    user_id: str = Depends(require_user),
):
    """
    Update the user's FCM device token.
    The Android app must call this whenever FirebaseMessagingService.onNewToken() fires.
    Without this, push notifications fail silently after app reinstalls or token rotations.
    """
    db = get_db()
    try:
        db.table("users") \
          .update({"fcm_token": body.fcm_token, "updated_at": "now()"}) \
          .eq("id", user_id) \
          .execute()
        return {"message": "FCM token updated"}
    except Exception as e:
        log.error("fcm_token_update_failed", user_id=user_id, error=str(e))
        raise HTTPException(500, "Failed to update FCM token")


