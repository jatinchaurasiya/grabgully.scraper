"""
services/price_tracker.py
──────────────────────────
Runs on a cron schedule (every 15 min).
Queries watchlist items where current_price <= target_price.
Sends FCM push notification + marks row as notified.
"""
from __future__ import annotations
import asyncio
from core.logger import get_logger
from services.db import get_pending_alerts, mark_alert_notified
from services.notifications import send_price_drop_notification

log = get_logger("price_tracker")


async def check_price_drops() -> int:
    """
    Main entry point called by APScheduler.
    Returns number of alerts fired.
    """
    log.info("price_drop_check_started")
    alerts = await get_pending_alerts()

    if not alerts:
        log.info("price_drop_check_done", alerts_fired=0)
        return 0

    fired = 0
    for alert in alerts:
        try:
            sent = await send_price_drop_notification(
                fcm_token     = alert.get("fcm_token", ""),
                product_title = alert["product_title"],
                platform      = alert["platform"],
                current_price = alert["current_price"],
                target_price  = alert["target_price"],
                affiliate_url = alert["affiliate_url"],
                listing_id    = alert["listing_id"],
            )
            if sent:
                await mark_alert_notified(alert["watchlist_id"])
                fired += 1
            # Small delay to avoid FCM rate limits
            await asyncio.sleep(0.2)
        except Exception as e:
            log.error("alert_fire_failed",
                      watchlist_id=alert.get("watchlist_id"), error=str(e))

    log.info("price_drop_check_done", alerts_fired=fired, total_pending=len(alerts))
    return fired
