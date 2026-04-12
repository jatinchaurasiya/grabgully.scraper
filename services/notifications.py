"""
services/notifications.py
──────────────────────────
Firebase Cloud Messaging — sends price drop push notifications to Android app.
Uses Firebase Admin SDK with service account auth.
"""
from __future__ import annotations
import json
import httpx
from typing import Optional
from core.config import get_settings
from core.logger import get_logger
from core.exceptions import NotificationError

log = get_logger("notifications")

# FCM v1 API endpoint
FCM_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"


async def _get_access_token() -> str:
    """
    Get a short-lived OAuth2 access token for FCM using the service account.
    Uses Google's token endpoint — no google-auth lib needed.
    """
    import time
    import json as _json
    from jose import jwt as _jwt

    s = get_settings()
    sa = _json.loads(s.firebase_service_account_json)

    now = int(time.time())
    claim = {
        "iss": sa["client_email"],
        "scope": "https://www.googleapis.com/auth/firebase.messaging",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }
    signed_jwt = _jwt.encode(claim, sa["private_key"], algorithm="RS256")

    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signed_jwt,
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]


async def send_price_drop_notification(
    fcm_token: str,
    product_title: str,
    platform: str,
    current_price: float,
    target_price: float,
    affiliate_url: str,
    listing_id: str,
) -> bool:
    """
    Send a price drop push notification to a specific device.
    Returns True on success, False on failure (non-fatal).
    """
    if not fcm_token:
        log.warning("send_notif_skipped", reason="no_fcm_token", listing_id=listing_id)
        return False

    s = get_settings()
    if not s.firebase_project_id or s.firebase_service_account_json == "{}":
        log.warning("firebase_not_configured")
        return False

    try:
        token = await _get_access_token()
        url = FCM_URL.format(project_id=s.firebase_project_id)

        payload = {
            "message": {
                "token": fcm_token,
                "notification": {
                    "title": "Price Gira! 🎉",
                    "body": f"{product_title[:60]} is now ₹{current_price:,.0f} on {platform.title()}",
                },
                "data": {
                    "type":          "price_drop",
                    "listing_id":    listing_id,
                    "current_price": str(current_price),
                    "target_price":  str(target_price),
                    "affiliate_url": affiliate_url,
                    "platform":      platform,
                },
                "android": {
                    "priority": "high",
                    "notification": {
                        "icon":          "ic_notification",
                        "color":         "#C9A84C",
                        "channel_id":    "price_drops",
                        "click_action":  "OPEN_COMPARE_SCREEN",
                    },
                },
            }
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type":  "application/json",
                },
            )
            if r.status_code == 200:
                log.info("push_sent", listing_id=listing_id, price=current_price)
                return True
            else:
                log.error("push_failed", status=r.status_code, body=r.text[:200])
                return False

    except Exception as e:
        log.error("push_exception", listing_id=listing_id, error=str(e))
        return False
