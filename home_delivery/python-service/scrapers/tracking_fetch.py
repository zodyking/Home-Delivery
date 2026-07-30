"""
Unified tracking fetch module.

Single entry point for carrier tracking that combines navigation, waiting,
and parsing. Both carrier probe and polling use this to ensure consistent
behavior — a carrier is only confirmed when real events are returned.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from carrier_detect import (
    CARRIERS,
    CarrierType,
    get_tracking_url,
    infer_carrier_from_format,
    normalize_tracking_number,
)

from .base import get_page, reset_browser_on_failure
from .navigation import (
    HTTP2_ERROR_MARKERS,
    goto_tracking_page,
    wait_for_ups_tracking,
    wait_for_usps_tracking,
)
from .parsers import parse_ups_tracking, parse_usps_tracking

logger = logging.getLogger(__name__)


async def _fetch_usps(page, tracking_number: str) -> dict[str, Any]:
    """Fetch USPS tracking data from a ready page."""
    url = get_tracking_url("usps", tracking_number)
    await goto_tracking_page(page, url, timeout_ms=60000)

    if not await wait_for_usps_tracking(page, timeout_ms=45000):
        title = await page.title()
        if "access denied" in title.lower():
            return {"error": "USPS blocked automated access (Access Denied)"}
        content = await page.content()
        if "bm-verify" in content.lower():
            return {"error": "USPS Akamai challenge not bypassed"}
        return {"error": "USPS tracking content not found"}

    return await parse_usps_tracking(page)


async def _fetch_ups(page, tracking_number: str) -> dict[str, Any]:
    """Fetch UPS tracking data from a ready page."""
    url = get_tracking_url("ups", tracking_number)
    await goto_tracking_page(page, url, timeout_ms=60000)

    if not await wait_for_ups_tracking(page, tracking_number, timeout_ms=30000):
        return {"error": "UPS tracking content not found"}

    return await parse_ups_tracking(page)


async def _fetch_fedex(page, tracking_number: str) -> dict[str, Any]:
    """Fetch FedEx tracking data from a ready page (stub for now)."""
    return {"error": "FedEx scraping not yet implemented"}


_FETCH_FUNCTIONS = {
    "usps": _fetch_usps,
    "ups": _fetch_ups,
    "fedex": _fetch_fedex,
}


async def fetch_carrier_tracking(
    carrier: CarrierType,
    tracking_number: str,
) -> dict[str, Any]:
    """
    Fetch tracking data for a specific carrier.

    Returns dict with:
        - On success: events, status, status_detail, last_polled, error=None
        - On failure: error string, possibly empty events

    Args:
        carrier: The carrier to fetch from (usps, ups, fedex).
        tracking_number: The tracking number.

    Returns:
        Tracking result dict.
    """
    normalized = normalize_tracking_number(tracking_number)
    fetch_fn = _FETCH_FUNCTIONS.get(carrier)

    if not fetch_fn:
        return {"error": f"Unknown carrier: {carrier}"}

    logger.info("Fetching %s tracking for %s", carrier.upper(), normalized)

    async def _do_fetch() -> dict[str, Any]:
        async with get_page(timeout_ms=70000) as page:
            return await fetch_fn(page, normalized)

    try:
        result = await _do_fetch()
    except Exception as exc:
        exc_str = str(exc)
        if any(marker in exc_str for marker in HTTP2_ERROR_MARKERS):
            logger.warning(
                "%s fetch hit transport error, resetting browser and retrying: %s",
                carrier.upper(),
                exc,
            )
            await reset_browser_on_failure()
            try:
                result = await _do_fetch()
            except Exception as retry_exc:
                logger.error(
                    "%s fetch retry failed for %s: %s",
                    carrier.upper(),
                    normalized,
                    retry_exc,
                )
                return {"error": str(retry_exc)}
        else:
            logger.error("%s fetch failed for %s: %s", carrier.upper(), normalized, exc)
            return {"error": str(exc)}

    events = result.get("events") or []
    logger.info(
        "%s fetch complete for %s: status=%s, events=%d, error=%s",
        carrier.upper(),
        normalized,
        result.get("status"),
        len(events),
        result.get("error"),
    )

    return result


async def fetch_tracking_auto(
    tracking_number: str,
) -> tuple[CarrierType | None, dict[str, Any]]:
    """
    Auto-detect carrier by attempting to fetch tracking data.

    Tries carriers in order: format-inferred carrier first, then others.
    Returns as soon as a carrier yields at least one tracking event.

    Args:
        tracking_number: The tracking number.

    Returns:
        Tuple of (carrier, result). Carrier is None if no carrier returned events.
        Result contains either successful tracking data or the last error.
    """
    normalized = normalize_tracking_number(tracking_number)
    if not normalized:
        return None, {"error": "Invalid tracking number"}

    # Build try order: format hint first, then remaining carriers
    hinted = infer_carrier_from_format(normalized)
    try_order: list[CarrierType] = []

    if hinted:
        try_order.append(hinted)
        logger.info(
            "Format inference suggests %s for %s, trying first",
            hinted.upper(),
            normalized,
        )

    for carrier in CARRIERS:
        if carrier not in try_order:
            try_order.append(carrier)

    last_error: dict[str, Any] = {"error": "No carrier returned tracking events"}

    for carrier in try_order:
        result = await fetch_carrier_tracking(carrier, normalized)

        if result.get("error"):
            last_error = result
            logger.debug(
                "%s fetch returned error for %s: %s",
                carrier.upper(),
                normalized,
                result.get("error"),
            )
            continue

        events = result.get("events") or []
        if events:
            logger.info(
                "Carrier confirmed via scrape: %s for %s (%d events)",
                carrier.upper(),
                normalized,
                len(events),
            )
            return carrier, result

        # No events but no error — might be "label created" with empty history
        # Still counts as a successful carrier detection if status is set
        status = result.get("status", "").lower()
        if status and status not in ("unknown", "pending", ""):
            logger.info(
                "Carrier confirmed via scrape (no events, status=%s): %s for %s",
                result.get("status"),
                carrier.upper(),
                normalized,
            )
            return carrier, result

        logger.debug(
            "%s returned no events and no meaningful status for %s",
            carrier.upper(),
            normalized,
        )
        last_error = {"error": f"{carrier.upper()} page loaded but no tracking data found"}

    logger.warning("No carrier returned tracking events for %s", normalized)
    return None, last_error
