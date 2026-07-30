"""
Carrier probe detection using Playwright.

Instead of regex-based detection, this module probes carrier tracking pages
to determine which carrier a tracking number belongs to by checking for
valid DOM elements.
"""
from __future__ import annotations

import logging
from typing import Literal

from carrier_detect import CarrierType, get_tracking_url, normalize_tracking_number
from scrapers.base import get_page

logger = logging.getLogger(__name__)

# Probe order: USPS is most common for e-commerce, then UPS, then FedEx
PROBE_ORDER: list[CarrierType] = ["usps", "ups", "fedex"]

# Timeout for probing (shorter than full scrape)
PROBE_TIMEOUT_MS = 15000


async def _probe_usps(page, tracking_number: str) -> bool:
    """
    Probe USPS tracking page to check if tracking number is valid.

    Returns True if the tracking page shows valid tracking content.
    """
    url = get_tracking_url("usps", tracking_number)
    logger.debug(f"Probing USPS: {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PROBE_TIMEOUT_MS)
        await page.wait_for_timeout(2000)

        # Check for valid tracking indicators
        valid_selectors = [
            ".tb-status",
            ".tracking-progress-bar-status-container",
            ".tb-step.current-step",
            "#trackingHistory",
        ]

        for selector in valid_selectors:
            if await page.locator(selector).count() > 0:
                # Verify it's not an error message
                page_text = await page.locator("body").text_content() or ""
                page_text_lower = page_text.lower()

                invalid_indicators = [
                    "not available",
                    "cannot be found",
                    "status not available",
                    "no record",
                    "enter a tracking number",
                ]

                if not any(ind in page_text_lower for ind in invalid_indicators):
                    logger.info(f"USPS probe successful for {tracking_number}")
                    return True

        logger.debug(f"USPS probe failed for {tracking_number}: no valid content")
        return False

    except Exception as e:
        logger.debug(f"USPS probe error for {tracking_number}: {e}")
        return False


async def _probe_ups(page, tracking_number: str) -> bool:
    """
    Probe UPS tracking page to check if tracking number is valid.

    Returns True if the tracking page shows valid tracking content.
    """
    url = get_tracking_url("ups", tracking_number)
    logger.debug(f"Probing UPS: {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PROBE_TIMEOUT_MS)
        await page.wait_for_timeout(3000)

        # Check for valid tracking indicators
        valid_selectors = [
            "#shipProg_act_0",
            ".ups-shipment-progress",
            "[id^='shipProg_act_']",
            ".track-results",
        ]

        for selector in valid_selectors:
            if await page.locator(selector).count() > 0:
                # Verify it's not an error message
                page_text = await page.locator("body").text_content() or ""
                page_text_lower = page_text.lower()

                invalid_indicators = [
                    "not found",
                    "invalid tracking number",
                    "we could not locate",
                    "no information available",
                    "unable to locate",
                ]

                if not any(ind in page_text_lower for ind in invalid_indicators):
                    logger.info(f"UPS probe successful for {tracking_number}")
                    return True

        logger.debug(f"UPS probe failed for {tracking_number}: no valid content")
        return False

    except Exception as e:
        logger.debug(f"UPS probe error for {tracking_number}: {e}")
        return False


async def _probe_fedex(page, tracking_number: str) -> bool:
    """
    Probe FedEx tracking page to check if tracking number is valid.

    FedEx is a JS SPA and needs extra wait time.

    Returns True if the tracking page shows valid tracking content.
    """
    url = get_tracking_url("fedex", tracking_number)
    logger.debug(f"Probing FedEx: {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PROBE_TIMEOUT_MS)
        await page.wait_for_timeout(4000)

        # Check for valid tracking indicators
        valid_selectors = [
            "[class*='shipment']",
            "[class*='tracking-status']",
            "[class*='shipmentStatus']",
            "[class*='travel-history']",
            "[class*='scan-event']",
        ]

        for selector in valid_selectors:
            if await page.locator(selector).count() > 0:
                # Verify it's not an error message
                page_text = await page.locator("body").text_content() or ""
                page_text_lower = page_text.lower()

                invalid_indicators = [
                    "not found",
                    "invalid tracking number",
                    "cannot locate",
                    "no results",
                    "unable to find",
                    "please verify",
                ]

                if not any(ind in page_text_lower for ind in invalid_indicators):
                    logger.info(f"FedEx probe successful for {tracking_number}")
                    return True

        logger.debug(f"FedEx probe failed for {tracking_number}: no valid content")
        return False

    except Exception as e:
        logger.debug(f"FedEx probe error for {tracking_number}: {e}")
        return False


# Probe functions by carrier
PROBE_FUNCTIONS = {
    "usps": _probe_usps,
    "ups": _probe_ups,
    "fedex": _probe_fedex,
}


async def probe_carrier(tracking_number: str) -> CarrierType | None:
    """
    Probe carriers to detect which one a tracking number belongs to.

    Attempts to load tracking pages for USPS, UPS, and FedEx in sequence,
    returning early when a valid carrier is found.

    Args:
        tracking_number: The tracking number to probe.

    Returns:
        The carrier type ('usps', 'ups', or 'fedex') or None if not found.
    """
    normalized = normalize_tracking_number(tracking_number)
    if not normalized:
        logger.warning("Empty tracking number provided to probe_carrier")
        return None

    logger.info(f"Probing carriers for tracking number: {normalized}")

    # Try each carrier in order, using a fresh page for each
    for carrier in PROBE_ORDER:
        probe_fn = PROBE_FUNCTIONS[carrier]

        try:
            async with get_page(timeout_ms=PROBE_TIMEOUT_MS + 5000) as page:
                if await probe_fn(page, normalized):
                    logger.info(f"Carrier detected: {carrier} for {normalized}")
                    return carrier
        except Exception as e:
            logger.warning(f"Probe failed for {carrier}/{normalized}: {e}")
            continue

    logger.warning(f"No carrier found for tracking number: {normalized}")
    return None


async def probe_carrier_result(tracking_number: str) -> dict:
    """
    Probe carriers and return a result dict with carrier info and tracking URL.

    Args:
        tracking_number: The tracking number to probe.

    Returns:
        Dict with carrier, tracking_number, and tracking_url, or error.
    """
    normalized = normalize_tracking_number(tracking_number)
    if not normalized:
        return {"error": "Invalid tracking number", "tracking_number": tracking_number}

    carrier = await probe_carrier(normalized)

    if not carrier:
        return {
            "error": "Could not find this tracking number at USPS, UPS, or FedEx",
            "tracking_number": normalized,
        }

    return {
        "carrier": carrier,
        "tracking_number": normalized,
        "tracking_url": get_tracking_url(carrier, normalized),
    }
