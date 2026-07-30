"""
Carrier detection via scrape-first approach.

Instead of probing carrier URLs with HTTP/browser checks and falling back to
format inference, we now attempt to actually fetch tracking data. A carrier
is only confirmed when real tracking events (or a meaningful status) are
returned from the scrape.

This eliminates the disconnect where carrier detection succeeds via format
inference but scraping later fails due to Akamai or other blocks.
"""
from __future__ import annotations

import logging

from carrier_detect import (
    CarrierType,
    get_tracking_url,
    normalize_tracking_number,
)
from scrapers.tracking_fetch import fetch_carrier_tracking, fetch_tracking_auto

logger = logging.getLogger(__name__)


async def probe_carrier(tracking_number: str) -> CarrierType | None:
    """
    Detect carrier by attempting to scrape tracking data.

    Returns the carrier only if real tracking data was retrieved.
    """
    carrier, _method = await probe_carrier_with_method(tracking_number)
    return carrier


async def probe_carrier_with_method(
    tracking_number: str,
) -> tuple[CarrierType | None, str]:
    """
    Detect carrier and return how it was detected.

    Returns:
        (carrier, method) where method is "scrape" if successful, empty string otherwise.
    """
    normalized = normalize_tracking_number(tracking_number)
    if not normalized:
        logger.warning("Empty tracking number provided to probe_carrier")
        return None, ""

    logger.info("Probing carrier via scrape-first for: %s", normalized)

    carrier, result = await fetch_tracking_auto(normalized)

    if carrier and not result.get("error"):
        events = result.get("events") or []
        logger.info(
            "Carrier detected via scrape: %s for %s (%d events)",
            carrier,
            normalized,
            len(events),
        )
        return carrier, "scrape"

    # No carrier confirmed via scrape
    error = result.get("error", "Unknown error")
    logger.warning(
        "No carrier confirmed via scrape for %s: %s",
        normalized,
        error,
    )
    return None, ""


async def probe_carrier_result(tracking_number: str) -> dict:
    """
    Probe carrier and return full result including tracking data.

    This is the main entry point for the probe endpoint. Unlike the old
    implementation that only returned carrier info, this returns the
    actual tracking data from the successful scrape.
    """
    normalized = normalize_tracking_number(tracking_number)
    if not normalized:
        return {"error": "Invalid tracking number", "tracking_number": tracking_number}

    carrier, result = await fetch_tracking_auto(normalized)

    if not carrier:
        error = result.get("error", "Could not determine carrier")
        return {
            "error": error,
            "tracking_number": normalized,
        }

    # Return carrier info plus the tracking data from the scrape
    return {
        "carrier": carrier,
        "tracking_number": normalized,
        "tracking_url": get_tracking_url(carrier, normalized),
        "detected_via": "scrape",
        "status": result.get("status"),
        "status_detail": result.get("status_detail"),
        "events": result.get("events") or [],
        "out_for_delivery": result.get("out_for_delivery", False),
        "delivered": result.get("delivered", False),
        "last_polled": result.get("last_polled"),
        "last_event_fingerprint": result.get("last_event_fingerprint"),
    }
