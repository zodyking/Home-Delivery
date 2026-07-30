"""
UPS tracking scraper using Playwright.
"""
from __future__ import annotations

import logging
from typing import Any

from carrier_detect import get_tracking_url

from .base import get_page, reset_browser_on_failure
from .navigation import goto_tracking_page, wait_for_ups_tracking
from .parsers import parse_ups_tracking

logger = logging.getLogger(__name__)


async def scrape_ups(tracking_number: str) -> dict[str, Any]:
    """Scrape UPS tracking page for package status and event history."""
    url = get_tracking_url("ups", tracking_number)
    logger.info("Scraping UPS: %s", tracking_number)

    async def _scrape() -> dict[str, Any]:
        async with get_page(timeout_ms=60000) as page:
            await goto_tracking_page(page, url, timeout_ms=60000)

            if not await wait_for_ups_tracking(page, tracking_number):
                logger.warning("UPS tracking content not found for %s", tracking_number)
                return {"error": "Tracking content not found"}

            result = await parse_ups_tracking(page)
            logger.info(
                "UPS scrape complete: status=%s, events=%s",
                result.get("status"),
                len(result.get("events") or []),
            )
            return result

    try:
        return await _scrape()
    except Exception as exc:
        if any(marker in str(exc) for marker in ("ERR_HTTP2", "ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED")):
            logger.warning("UPS scrape hit transport error, resetting browser and retrying once: %s", exc)
            await reset_browser_on_failure()
            try:
                return await _scrape()
            except Exception as retry_exc:
                logger.error("UPS scrape retry failed for %s: %s", tracking_number, retry_exc)
                return {"error": str(retry_exc)}
        logger.error("UPS scrape failed for %s: %s", tracking_number, exc)
        return {"error": str(exc)}
