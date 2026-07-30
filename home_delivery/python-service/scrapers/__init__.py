"""
Package tracking scrapers for USPS, UPS, and FedEx.
Uses Playwright for headless browser scraping.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _scrape_by_carrier(carrier: str, tracking_number: str) -> dict[str, Any] | None:
    """Execute the scraper for a specific carrier."""
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


async def scrape_package(
    package: dict[str, Any],
    allow_reprobe: bool = True,
) -> dict[str, Any] | None:
    """
    Scrape tracking information for a package.

    If scraping fails and allow_reprobe is True, attempts to re-probe the carrier
    and retry with the correct one if it differs.

    Args:
        package: Package dict with carrier and tracking_number.
        allow_reprobe: Whether to re-probe carrier on failure (default True).

    Returns:
        Dict with updated status, events, etc. or None if no changes.
        If carrier was re-probed and changed, includes 'carrier_changed' key.
    """
    carrier = package.get("carrier")
    tracking_number = package.get("tracking_number")

    if not carrier or not tracking_number:
        logger.warning("Invalid package: missing carrier or tracking_number")
        return None

    try:
        result = await _scrape_by_carrier(carrier, tracking_number)

        # If scrape returned an error, try re-probing
        if result and result.get("error") and allow_reprobe:
            return await _handle_scrape_error(package, result)

        return result

    except Exception as e:
        logger.error(f"Scrape failed for {carrier}/{tracking_number}: {e}")

        # On exception, try re-probing the carrier
        if allow_reprobe:
            reprobe_result = await _try_reprobe(package)
            if reprobe_result:
                return reprobe_result

        raise


async def _handle_scrape_error(
    package: dict[str, Any],
    error_result: dict[str, Any],
) -> dict[str, Any] | None:
    """Handle scrape error by attempting to re-probe carrier."""
    carrier = package.get("carrier")
    tracking_number = package.get("tracking_number")

    logger.info(f"Scrape returned error for {carrier}/{tracking_number}, attempting re-probe")

    reprobe_result = await _try_reprobe(package)
    if reprobe_result:
        return reprobe_result

    return error_result


async def _try_reprobe(package: dict[str, Any]) -> dict[str, Any] | None:
    """
    Try to re-probe the carrier and retry scrape if carrier differs.

    Returns:
        Updated result with carrier_changed flag, or None if reprobe unsuccessful.
    """
    from carrier_probe import probe_carrier
    from carrier_detect import get_tracking_url

    carrier = package.get("carrier")
    tracking_number = package.get("tracking_number")

    try:
        new_carrier = await probe_carrier(tracking_number)

        if not new_carrier:
            logger.warning(f"Re-probe found no carrier for {tracking_number}")
            return None

        if new_carrier == carrier:
            logger.info(f"Re-probe confirmed carrier {carrier} for {tracking_number}")
            return None

        logger.info(f"Re-probe detected carrier change: {carrier} -> {new_carrier} for {tracking_number}")

        # Retry scrape with new carrier (disable reprobe to avoid infinite loop)
        result = await _scrape_by_carrier(new_carrier, tracking_number)

        if result and not result.get("error"):
            result["carrier_changed"] = True
            result["new_carrier"] = new_carrier
            result["new_tracking_url"] = get_tracking_url(new_carrier, tracking_number)
            return result

        return None

    except Exception as e:
        logger.error(f"Re-probe failed for {tracking_number}: {e}")
        return None
