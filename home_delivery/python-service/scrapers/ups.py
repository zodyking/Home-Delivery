"""
UPS tracking scraper using Playwright.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

from .base import get_page

logger = logging.getLogger(__name__)


def _compute_fingerprint(events: list[dict]) -> str:
    """Compute a fingerprint from the latest event for change detection."""
    if not events:
        return ""
    latest = events[0]
    data = f"{latest.get('date', '')}{latest.get('description', '')}"
    return hashlib.md5(data.encode()).hexdigest()[:12]


def _is_out_for_delivery(milestone: str, activity: str) -> bool:
    """Check if status indicates out for delivery."""
    combined = f"{milestone} {activity}".lower()
    return "out for delivery" in combined


def _is_delivered(milestone: str, activity: str) -> bool:
    """Check if status indicates delivered."""
    combined = f"{milestone} {activity}".lower()
    return "delivered" in combined and "out for delivery" not in combined


async def scrape_ups(tracking_number: str) -> dict[str, Any]:
    """
    Scrape UPS tracking page for package status.

    Args:
        tracking_number: UPS tracking number.

    Returns:
        Dict with status, status_detail, events, out_for_delivery, delivered, etc.
    """
    url = f"https://www.ups.com/track?tracknum={tracking_number}&loc=en_US&requester=ST"
    logger.info(f"Scraping UPS: {tracking_number}")

    async with get_page(timeout_ms=45000) as page:
        await page.goto(url, wait_until="networkidle")

        # Wait for tracking content
        try:
            await page.wait_for_selector("[id^='shipProg_act_'], .ups-shipment-progress", timeout=20000)
        except Exception:
            logger.warning(f"UPS tracking content not found for {tracking_number}")
            return {"error": "Tracking content not found"}

        # Try to expand details drawer if collapsed
        try:
            drawer_buttons = page.locator("button:has-text('Show Details'), button:has-text('Details')")
            if await drawer_buttons.count() > 0:
                await drawer_buttons.first.click()
                await page.wait_for_timeout(1000)
        except Exception as e:
            logger.debug(f"Details drawer: {e}")

        # Parse tracking events by looking for shipProg_act_* elements
        events: list[dict[str, Any]] = []
        status = ""
        status_detail = ""

        try:
            # Find all activity entries by incrementing index
            for i in range(50):  # Max 50 events
                date_el = page.locator(f"#shipProg_act_Date{i}")
                if await date_el.count() == 0:
                    break

                event: dict[str, Any] = {}

                # Date
                date_text = await date_el.text_content()
                if date_text:
                    event["date"] = date_text.strip()

                # Time
                time_el = page.locator(f"#shipProg_act_Time{i}")
                if await time_el.count() > 0:
                    time_text = await time_el.text_content()
                    if time_text:
                        event["time"] = time_text.strip()
                        if event.get("date"):
                            event["date"] = f"{event['date']} {time_text.strip()}"

                # Milestone (major status)
                milestone_el = page.locator(f"#shipProg_act_Milestone{i}")
                milestone = ""
                if await milestone_el.count() > 0:
                    milestone = (await milestone_el.text_content() or "").strip()

                # Activity scan (detailed status)
                activity_el = page.locator(f"#shipProg_act_ActivityScan{i}")
                activity = ""
                if await activity_el.count() > 0:
                    activity = (await activity_el.text_content() or "").strip()

                event["description"] = milestone or activity
                if milestone and activity and milestone != activity:
                    event["description"] = f"{milestone} - {activity}"

                # Location
                loc_el = page.locator(f"#shipProg_act_Location{i}")
                if await loc_el.count() > 0:
                    event["location"] = (await loc_el.text_content() or "").strip()

                if event.get("description"):
                    events.append(event)

                # First event is current status
                if i == 0:
                    status = milestone or activity
                    status_detail = activity if milestone else ""

        except Exception as e:
            logger.error(f"Error parsing UPS events: {e}")

        out_for_delivery = _is_out_for_delivery(status, status_detail)
        delivered = _is_delivered(status, status_detail)

        result = {
            "status": status or "Unknown",
            "status_detail": status_detail,
            "events": events,
            "last_event_fingerprint": _compute_fingerprint(events),
            "out_for_delivery": out_for_delivery,
            "delivered": delivered,
            "last_polled": datetime.now(timezone.utc).isoformat(),
            "error": None,
        }

        if delivered:
            result["delivered_at"] = datetime.now(timezone.utc).isoformat()

        logger.info(f"UPS scrape complete: status={status}, events={len(events)}")
        return result
