"""
USPS tracking scraper using Playwright.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from carrier_detect import get_tracking_url

from .base import get_page

logger = logging.getLogger(__name__)


def _compute_fingerprint(events: list[dict]) -> str:
    """Compute a fingerprint from the latest event for change detection."""
    if not events:
        return ""
    latest = events[0]
    data = f"{latest.get('date', '')}{latest.get('description', '')}"
    return hashlib.md5(data.encode()).hexdigest()[:12]


def _is_out_for_delivery(status: str, status_detail: str) -> bool:
    """Check if status indicates out for delivery."""
    combined = f"{status} {status_detail}".lower()
    return "out for delivery" in combined


def _is_delivered(status: str, status_detail: str) -> bool:
    """Check if status indicates delivered."""
    combined = f"{status} {status_detail}".lower()
    return "delivered" in combined and "out for delivery" not in combined


async def scrape_usps(tracking_number: str) -> dict[str, Any]:
    """
    Scrape USPS tracking page for package status.

    Args:
        tracking_number: USPS tracking number.

    Returns:
        Dict with status, status_detail, events, out_for_delivery, delivered, etc.
    """
    url = get_tracking_url("usps", tracking_number)
    logger.info(f"Scraping USPS: {tracking_number}")

    async with get_page() as page:
        await page.goto(url, wait_until="domcontentloaded")

        # Wait for tracking content to load
        try:
            await page.wait_for_selector(".tb-status, .tracking-progress-bar-status-container", timeout=15000)
        except Exception:
            logger.warning(f"USPS tracking content not found for {tracking_number}")
            return {"error": "Tracking content not found"}

        # Expand full history via "Show Tracking History"
        try:
            show_history = page.locator("a.expand-collapse-history")
            if await show_history.count() > 0:
                text = (await show_history.first.text_content() or "").strip().lower()
                if "show tracking history" in text or text == "show":
                    await show_history.first.click()
                    await page.wait_for_timeout(800)
        except Exception as e:
            logger.debug(f"History toggle: {e}")

        # Parse current status from the main status area
        status = ""
        status_detail = ""

        try:
            current_step = page.locator(".tb-step.current-step").first
            status_el = current_step.locator(".tb-status")
            detail_el = current_step.locator(".tb-status-detail")

            if await status_el.count() > 0:
                status = (await status_el.text_content() or "").strip()
            if await detail_el.count() > 0:
                status_detail = (await detail_el.text_content() or "").strip()
        except Exception as e:
            logger.debug(f"Error getting current status: {e}")

        # Parse all tracking events
        events: list[dict[str, Any]] = []
        try:
            steps = page.locator(".tb-step")
            count = await steps.count()

            for i in range(count):
                step = steps.nth(i)

                # Skip the toggle container
                if await step.locator(".expand-collapse-history").count() > 0:
                    continue

                event: dict[str, Any] = {}

                # Get description
                desc_el = step.locator(".tb-status-detail")
                if await desc_el.count() > 0:
                    event["description"] = (await desc_el.text_content() or "").strip()

                # Get location
                loc_el = step.locator(".tb-location")
                if await loc_el.count() > 0:
                    event["location"] = (await loc_el.text_content() or "").strip()

                # Get date/time
                date_el = step.locator(".tb-date")
                if await date_el.count() > 0:
                    date_text = (await date_el.text_content() or "").strip()
                    event["date"] = date_text

                if event.get("description"):
                    events.append(event)

        except Exception as e:
            logger.error(f"Error parsing USPS events: {e}")

        # If no status from current-step, try first event
        if not status and events:
            status = events[0].get("description", "")

        out_for_delivery = _is_out_for_delivery(status, status_detail)
        delivered = _is_delivered(status, status_detail)

        result = {
            "status": status or status_detail or "Unknown",
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

        logger.info(f"USPS scrape complete: status={status}, events={len(events)}")
        return result
