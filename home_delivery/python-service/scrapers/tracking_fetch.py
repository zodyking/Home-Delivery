"""
Unified tracking fetch module.

Single entry point for carrier tracking that combines navigation, waiting,
and parsing. Both carrier probe and polling use this to ensure consistent
behavior — a carrier is only confirmed when real events are returned.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from carrier_detect import (
    CarrierType,
    auto_detect_carrier_order,
    get_tracking_url,
    normalize_tracking_number,
)

from .base import get_page, reset_browser_on_failure
from .navigation import (
    HTTP2_ERROR_MARKERS,
    goto_tracking_page,
    prepare_ups_tracking_page,
    wait_for_estes_tracking,
    wait_for_ups_tracking,
    wait_for_usps_tracking,
)
from .parsers import parse_estes_tracking, parse_ups_tracking, parse_usps_tracking

logger = logging.getLogger(__name__)

# Hard cap so add-package / refresh never block the UI for many minutes.
CARRIER_FETCH_TIMEOUTS: dict[str, float] = {
    "ups": 90.0,
    "usps": 180.0,
    "estes": 75.0,
}

UPS_WAIT_MS = 45000
UPS_PAGE_TIMEOUT_MS = 75000


def _needs_browser_retry(payload: dict[str, Any]) -> bool:
    err = (payload.get("error") or "").lower()
    return any(
        marker in err
        for marker in (
            "akamai",
            "access denied",
            "tracking content not found",
            "blocked automated",
            "timed out",
        )
    )


async def _fetch_usps(page, tracking_number: str) -> dict[str, Any]:
    """Fetch USPS tracking data from a ready page."""
    from .navigation import dismiss_usps_overlays, warmup_usps_session

    await warmup_usps_session(page)

    urls = [
        get_tracking_url("usps", tracking_number),
        # Official deep-link used by Informed Delivery / USPS UI
        f"https://tools.usps.com/tracking/?qtc_tLabels1={tracking_number}",
    ]

    last_error = "USPS tracking content not found"
    for index, url in enumerate(urls):
        await goto_tracking_page(page, url, timeout_ms=60000)
        await dismiss_usps_overlays(page)

        if await wait_for_usps_tracking(page, timeout_ms=60000):
            return await parse_usps_tracking(page)

        title = (await page.title()).lower()
        content = (await page.content()).lower()
        if "access denied" in title:
            last_error = "USPS blocked automated access (Access Denied)"
        elif "bm-verify" in content or "interstitialchallenge" in content:
            last_error = "USPS Akamai challenge not bypassed"
        else:
            last_error = "USPS tracking content not found"

        if index + 1 < len(urls):
            logger.info("USPS primary URL failed (%s); trying alternate deep-link", last_error)

    return {"error": last_error}


async def _fetch_ups(page, tracking_number: str) -> dict[str, Any]:
    """
    Single-pass UPS fetch — same path as agent files/test_tracking_scrape.py.

    No ups.com warm-up (often ERR_HTTP2_PROTOCOL_ERROR in HA containers and
    wastes half the timeout budget without helping the track-details page).
    """
    url = get_tracking_url("ups", tracking_number)
    await goto_tracking_page(page, url, timeout_ms=UPS_WAIT_MS)
    await prepare_ups_tracking_page(page, tracking_number)

    if await wait_for_ups_tracking(page, tracking_number, timeout_ms=UPS_WAIT_MS):
        return await parse_ups_tracking(page)

    return {"error": "UPS tracking content not found"}


async def _fetch_fedex(page, tracking_number: str) -> dict[str, Any]:
    """Fetch FedEx tracking data from a ready page (stub for now)."""
    _ = page, tracking_number
    return {"error": "FedEx scraping not yet implemented"}


async def _fetch_estes(page, tracking_number: str) -> dict[str, Any]:
    """Fetch Estes Express tracking data from My Estes shipment tracking."""
    from .navigation import prepare_estes_tracking_page

    url = get_tracking_url("estes", tracking_number)
    await goto_tracking_page(page, url, timeout_ms=45000)
    await prepare_estes_tracking_page(page, tracking_number)

    state = await wait_for_estes_tracking(page, timeout_ms=25000)
    if state == "not_found":
        return {"error": "Not found or tracking information unavailable"}
    if state != "ready":
        content = (await page.content()).lower()
        if "not found or tracking information unavailable" in content:
            return {"error": "Not found or tracking information unavailable"}
        return {"error": "Estes tracking content not found"}

    return await parse_estes_tracking(page)


_FETCH_FUNCTIONS = {
    "usps": _fetch_usps,
    "ups": _fetch_ups,
    "fedex": _fetch_fedex,
    "estes": _fetch_estes,
}


async def _fetch_carrier_tracking_impl(
    carrier: CarrierType,
    normalized: str,
) -> dict[str, Any]:
    fetch_fn = _FETCH_FUNCTIONS.get(carrier)
    if not fetch_fn:
        return {"error": f"Unknown carrier: {carrier}"}

    logger.info("Fetching %s tracking for %s", carrier.upper(), normalized)

    # Don't launch a browser for unimplemented carriers (FedEx stub).
    if carrier == "fedex":
        return await fetch_fn(None, normalized)

    async def _do_fetch() -> dict[str, Any]:
        page_timeout_ms = UPS_PAGE_TIMEOUT_MS if carrier == "ups" else 70000
        async with get_page(timeout_ms=page_timeout_ms) as page:
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

    # USPS (and occasionally UPS) can stick on a cold Akamai/browser state —
    # one full browser reset often clears it in Home Assistant containers.
    if (
        carrier in ("usps", "ups")
        and _needs_browser_retry(result)
        and not (result.get("events") or [])
    ):
        logger.warning(
            "%s fetch blocked (%s); resetting browser and retrying once",
            carrier.upper(),
            result.get("error"),
        )
        await reset_browser_on_failure()
        try:
            result = await _do_fetch()
        except Exception as retry_exc:
            logger.error(
                "%s browser-reset retry failed for %s: %s",
                carrier.upper(),
                normalized,
                retry_exc,
            )
            return {"error": str(retry_exc)}

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
    if not normalized:
        return {"error": "Invalid tracking number"}

    timeout = CARRIER_FETCH_TIMEOUTS.get(carrier)
    if timeout is None:
        return await _fetch_carrier_tracking_impl(carrier, normalized)

    async def _run_timed() -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                _fetch_carrier_tracking_impl(carrier, normalized),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "%s fetch timed out after %.0fs for %s",
                carrier.upper(),
                timeout,
                normalized,
            )
            return {
                "error": (
                    f"{carrier.upper()} tracking timed out — "
                    "package saved; will retry on next poll"
                ),
                "status": "Pending",
            }

    result = await _run_timed()

    # Outer retry after hard timeout — inner impl may not get a browser reset.
    if (
        carrier == "ups"
        and not (result.get("events") or [])
        and _needs_browser_retry(result)
    ):
        logger.warning(
            "UPS fetch missed after timeout (%s); resetting browser for one retry",
            result.get("error"),
        )
        await reset_browser_on_failure()
        result = await _run_timed()

    return result


async def fetch_tracking_auto(
    tracking_number: str,
) -> tuple[CarrierType | None, dict[str, Any]]:
    """
    Auto-detect carrier by attempting to fetch tracking data.

    Tries carriers one at a time until one returns real tracking data.
    Unambiguous formats (1Z → UPS, 10-digit → Estes, long IMpb → USPS) only
    try that carrier so a miss never surfaces as another carrier's error.

    Args:
        tracking_number: The tracking number.

    Returns:
        Tuple of (carrier, result). Carrier is None if no carrier returned events.
    """
    normalized = normalize_tracking_number(tracking_number)
    if not normalized:
        return None, {"error": "Invalid tracking number"}

    try_order = auto_detect_carrier_order(normalized)
    logger.info(
        "Auto-detect order for %s: %s",
        normalized,
        " → ".join(c.upper() for c in try_order),
    )

    last_error: dict[str, Any] = {"error": "No carrier returned tracking events"}

    for carrier in try_order:
        result = await fetch_carrier_tracking(carrier, normalized)

        if result.get("error"):
            last_error = result
            logger.info(
                "%s did not match %s (%s); trying next",
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

        # No events but no error — meaningful status still confirms carrier
        status = (result.get("status") or "").lower()
        if status and status not in ("unknown", "pending", ""):
            logger.info(
                "Carrier confirmed via scrape (no events, status=%s): %s for %s",
                result.get("status"),
                carrier.upper(),
                normalized,
            )
            return carrier, result

        logger.info(
            "%s returned no events/status for %s; trying next",
            carrier.upper(),
            normalized,
        )
        last_error = {"error": f"{carrier.upper()} page loaded but no tracking data found"}

    logger.warning(
        "No carrier returned tracking events for %s (last=%s)",
        normalized,
        last_error.get("error"),
    )
    # Never leak a single-carrier miss (e.g. Estes) as the user-facing error
    # after a multi-carrier probe — that looks like the wrong carrier won.
    return None, {
        "error": (
            "Could not determine carrier for this tracking number. "
            "Check the number and try again."
        ),
        "last_carrier_error": last_error.get("error"),
    }
