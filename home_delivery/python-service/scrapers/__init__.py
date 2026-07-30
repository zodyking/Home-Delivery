"""
Package tracking scrapers for USPS, UPS, FedEx, and Estes.
Uses the unified tracking_fetch module for consistent behavior.
"""
from __future__ import annotations

import logging
from typing import Any

from .tracking_fetch import fetch_carrier_tracking, fetch_tracking_auto

logger = logging.getLogger(__name__)


async def scrape_package(
    package: dict[str, Any],
    allow_reprobe: bool = True,
) -> dict[str, Any] | None:
    """
    Scrape tracking information for a package.

    If scraping fails and allow_reprobe is True, attempts to auto-detect
    the correct carrier and retry.

    Args:
        package: Package dict with carrier and tracking_number.
        allow_reprobe: Whether to try other carriers on failure (default True).

    Returns:
        Dict with updated status, events, etc. or None if no changes.
        If carrier was re-detected and changed, includes 'carrier_changed' key.
    """
    carrier = package.get("carrier")
    tracking_number = package.get("tracking_number")

    if not carrier or not tracking_number:
        logger.warning("Invalid package: missing carrier or tracking_number")
        return None

    try:
        result = await fetch_carrier_tracking(carrier, tracking_number)

        # If fetch returned an error and reprobe is allowed, try auto-detect
        if result.get("error") and allow_reprobe:
            return await _handle_fetch_error(package, result)

        return result

    except Exception as e:
        logger.error(f"Scrape failed for {carrier}/{tracking_number}: {e}")

        if allow_reprobe:
            reprobe_result = await _try_reprobe(package)
            if reprobe_result:
                return reprobe_result

        return {"error": str(e)}


async def _handle_fetch_error(
    package: dict[str, Any],
    error_result: dict[str, Any],
) -> dict[str, Any] | None:
    """Handle fetch error by attempting to re-probe carrier."""
    carrier = package.get("carrier")
    tracking_number = package.get("tracking_number")

    logger.info(
        "Fetch returned error for %s/%s: %s, attempting re-probe",
        carrier,
        tracking_number,
        error_result.get("error"),
    )

    reprobe_result = await _try_reprobe(package)
    if reprobe_result:
        return reprobe_result

    return error_result


async def _try_reprobe(package: dict[str, Any]) -> dict[str, Any] | None:
    """
    Try to auto-detect the carrier and retry fetch if carrier differs.

    Returns:
        Updated result with carrier_changed flag, or None if reprobe unsuccessful.
    """
    from carrier_detect import get_tracking_url

    carrier = package.get("carrier")
    tracking_number = package.get("tracking_number")

    try:
        new_carrier, result = await fetch_tracking_auto(tracking_number)

        if not new_carrier:
            logger.warning("Re-probe found no carrier for %s", tracking_number)
            return None

        if new_carrier == carrier:
            logger.info("Re-probe confirmed carrier %s for %s", carrier, tracking_number)
            # Still return the result even if carrier didn't change
            if not result.get("error"):
                return result
            return None

        logger.info(
            "Re-probe detected carrier change: %s -> %s for %s",
            carrier,
            new_carrier,
            tracking_number,
        )

        if result and not result.get("error"):
            result["carrier_changed"] = True
            result["new_carrier"] = new_carrier
            result["new_tracking_url"] = get_tracking_url(new_carrier, tracking_number)
            return result

        return None

    except Exception as e:
        logger.error("Re-probe failed for %s: %s", tracking_number, e)
        return None
