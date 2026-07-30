"""
Package tracking scrapers for USPS, UPS, and FedEx.
Uses Playwright for headless browser scraping.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def scrape_package(package: dict[str, Any]) -> dict[str, Any] | None:
    """
    Scrape tracking information for a package.

    Args:
        package: Package dict with carrier and tracking_number.

    Returns:
        Dict with updated status, events, etc. or None if no changes.
    """
    carrier = package.get("carrier")
    tracking_number = package.get("tracking_number")

    if not carrier or not tracking_number:
        logger.warning(f"Invalid package: missing carrier or tracking_number")
        return None

    try:
        if carrier == "usps":
            from .usps import scrape_usps
            return await scrape_usps(tracking_number)
        elif carrier == "ups":
            from .ups import scrape_ups
            return await scrape_ups(tracking_number)
        elif carrier == "fedex":
            from .fedex import scrape_fedex
            return await scrape_fedex(tracking_number)
        else:
            logger.warning(f"Unknown carrier: {carrier}")
            return None
    except Exception as e:
        logger.error(f"Scrape failed for {carrier}/{tracking_number}: {e}")
        raise
