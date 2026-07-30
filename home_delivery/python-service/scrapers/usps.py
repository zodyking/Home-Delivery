"""
USPS tracking scraper using Playwright.
"""
from __future__ import annotations

import logging
from typing import Any

from carrier_detect import get_tracking_url

from .base import get_page, reset_browser_on_failure
from .navigation import goto_tracking_page, wait_for_usps_tracking
from .parsers import parse_usps_tracking

logger = logging.getLogger(__name__)


async def scrape_usps(tracking_number: str) -> dict[str, Any]:
    """Scrape USPS tracking page for package status and event history."""
    url = get_tracking_url("usps", tracking_number)
    logger.info("Scraping USPS: %s", tracking_number)

    async def _scrape() -> dict[str, Any]:
        async with get_page(timeout_ms=60000) as page:
            await goto_tracking_page(page, url, timeout_ms=60000)

            if not await wait_for_usps_tracking(page):
                title = await page.title()
                if "access denied" in title.lower():
                    return {"error": "USPS blocked automated access (Access Denied)"}
                logger.warning("USPS tracking content not found for %s", tracking_number)
                return {"error": "Tracking content not found"}

            result = await parse_usps_tracking(page)
            logger.info(
                "USPS scrape complete: status=%s, events=%s",
                result.get("status"),
                len(result.get("events") or []),
            )
            return result

    try:
        return await _scrape()
    except Exception as exc:
        logger.warning("USPS scrape error for %s, resetting browser and retrying once: %s", tracking_number, exc)
        await reset_browser_on_failure()
        try:
            return await _scrape()
        except Exception as retry_exc:
            logger.error("USPS scrape retry failed for %s: %s", tracking_number, retry_exc)
            return {"error": str(retry_exc)}
