"""
FedEx tracking scraper using Playwright.
FedEx is a JS SPA - requires waiting for XHR render.
"""
from __future__ import annotations

import hashlib
import logging
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


def _is_out_for_delivery(status: str) -> bool:
    """Check if status indicates out for delivery."""
    return "out for delivery" in status.lower()


def _is_delivered(status: str) -> bool:
    """Check if status indicates delivered."""
    lower = status.lower()
    return "delivered" in lower and "out for delivery" not in lower


async def scrape_fedex(tracking_number: str) -> dict[str, Any]:
    """
    Scrape FedEx tracking page for package status.

    FedEx is a fully JS-rendered SPA. We need to:
    1. Navigate and wait for XHR to complete
    2. Click "Travel history" to expand events
    3. Parse the rendered DOM

    Args:
        tracking_number: FedEx tracking number.

    Returns:
        Dict with status, status_detail, events, out_for_delivery, delivered, etc.
    """
    url = f"https://www.fedex.com/fedextrack/?trknbr={tracking_number}"
    logger.info(f"Scraping FedEx: {tracking_number}")

    async with get_page(timeout_ms=60000) as page:
        await page.goto(url, wait_until="networkidle")

        # FedEx SPA needs extra time for XHR render
        await page.wait_for_timeout(4000)

        # Wait for any tracking content
        try:
            await page.wait_for_selector(
                "[class*='shipment'], [class*='tracking'], [class*='status']",
                timeout=20000
            )
        except Exception:
            logger.warning(f"FedEx tracking content not found for {tracking_number}")
            return {"error": "Tracking content not found - page may require login or is blocked"}

        # Try to expand travel history
        try:
            travel_history = page.get_by_text("Travel history", exact=False)
            if await travel_history.count() > 0:
                await travel_history.first.click()
                await page.wait_for_timeout(1500)
        except Exception as e:
            logger.debug(f"Travel history toggle: {e}")

        events: list[dict[str, Any]] = []
        status = ""
        status_detail = ""

        # FedEx DOM structure varies - try multiple selectors
        try:
            # Look for status heading
            status_selectors = [
                "h2[class*='status']",
                "[class*='shipmentStatus']",
                "[class*='tracking-status']",
                "div[class*='headline']",
            ]

            for selector in status_selectors:
                el = page.locator(selector).first
                if await el.count() > 0:
                    text = await el.text_content()
                    if text and text.strip():
                        status = text.strip()
                        break

            # Look for delivery date/time info
            delivery_selectors = [
                "[class*='delivery-date']",
                "[class*='deliveryDate']",
                "[class*='scheduled']",
            ]

            for selector in delivery_selectors:
                el = page.locator(selector).first
                if await el.count() > 0:
                    text = await el.text_content()
                    if text and text.strip():
                        status_detail = text.strip()
                        break

            # Parse travel history events
            # FedEx uses various class names for timeline
            event_selectors = [
                "[class*='travel-history'] [class*='event']",
                "[class*='travelHistory'] li",
                "[class*='scan-event']",
                "[class*='timeline'] [class*='item']",
            ]

            for selector in event_selectors:
                items = page.locator(selector)
                count = await items.count()

                if count > 0:
                    for i in range(min(count, 50)):
                        item = items.nth(i)
                        event: dict[str, Any] = {}

                        # Get all text content and try to parse
                        text = await item.text_content()
                        if text:
                            event["description"] = text.strip()

                        # Try to find date within the item
                        date_el = item.locator("[class*='date'], time")
                        if await date_el.count() > 0:
                            event["date"] = (await date_el.first.text_content() or "").strip()

                        # Try to find location
                        loc_el = item.locator("[class*='location']")
                        if await loc_el.count() > 0:
                            event["location"] = (await loc_el.first.text_content() or "").strip()

                        if event.get("description"):
                            events.append(event)

                    if events:
                        break

        except Exception as e:
            logger.error(f"Error parsing FedEx events: {e}")

        # If no status found, try to get from page title or first visible text
        if not status:
            try:
                title = await page.title()
                if title and tracking_number in title:
                    status = "Tracking Available"
            except Exception:
                pass

        out_for_delivery = _is_out_for_delivery(status)
        delivered = _is_delivered(status)

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

        logger.info(f"FedEx scrape complete: status={status}, events={len(events)}")
        return result
