"""
Carrier detection by probing carrier tracking links.

HTTP checks run first for speed; Playwright browser probes confirm JS-heavy
pages (USPS Akamai, Estes My Estes, etc.). Format inference is only used as a
last resort when link probing is blocked or inconclusive.

Full tracking data is fetched separately via fetch_carrier_tracking after the
carrier is identified (add-package, polling).
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from carrier_detect import (
    IMPLEMENTED_CARRIERS,
    CarrierType,
    get_tracking_url,
    infer_carrier_from_format,
    normalize_tracking_number,
)
from scrapers.base import get_page
from scrapers.navigation import (
    goto_tracking_page,
    prepare_estes_tracking_page,
    wait_for_estes_tracking,
)

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SEC = 8
PROBE_TIMEOUT_MS = 12000

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _html_indicates_blocked(html: str) -> bool:
    lower = html.lower()
    return any(
        phrase in lower
        for phrase in (
            "access denied",
            "permission to access",
            "errors.edgesuite.net",
            "please enable javascript",
            "bm-verify",
            "interstitialchallenge",
            "/_sec/verify",
        )
    )


def _html_has_tracking_status(html: str, tracking_number: str) -> bool:
    lower = html.lower()
    number = tracking_number.lower()

    if number in lower:
        return True

    return any(
        phrase in lower
        for phrase in (
            "out for delivery",
            "delivered",
            "in transit",
            "pre-shipment",
            "shipment ready",
            "on the way",
            "departed",
            "arrived at",
        )
    )


async def _probe_usps_http(session: aiohttp.ClientSession, tracking_number: str) -> bool:
    url = get_tracking_url("usps", tracking_number)
    try:
        async with session.get(url, allow_redirects=True) as response:
            if response.status >= 400:
                return False
            html = await response.text()
            if _html_indicates_blocked(html):
                return False
            if "status not available" in html.lower() and "tb-status" not in html.lower():
                return False
            if "tb-status" in html.lower() or "tracking-progress-bar-status-container" in html.lower():
                return _html_has_tracking_status(html, tracking_number)
    except Exception as exc:
        logger.debug("USPS HTTP probe error for %s: %s", tracking_number, exc)
    return False


async def _probe_ups_http(session: aiohttp.ClientSession, tracking_number: str) -> bool:
    url = get_tracking_url("ups", tracking_number)
    try:
        async with session.get(url, allow_redirects=True) as response:
            if response.status >= 400:
                return False
            html = await response.text()
            if _html_indicates_blocked(html):
                return False
            lower = html.lower()
            if "shipprog_act_date0" in lower or "ups-shipment-progress" in lower:
                return _html_has_tracking_status(html, tracking_number)
            if "invalid tracking number" in lower or "we could not locate" in lower:
                return False
    except Exception as exc:
        logger.debug("UPS HTTP probe error for %s: %s", tracking_number, exc)
    return False


async def _probe_estes_http(session: aiohttp.ClientSession, tracking_number: str) -> bool:
    url = get_tracking_url("estes", tracking_number)
    try:
        async with session.get(url, allow_redirects=True) as response:
            if response.status >= 400:
                return False
            html = await response.text()
            if _html_indicates_blocked(html):
                return False
            lower = html.lower()
            if "not found or tracking information unavailable" in lower:
                return False
            if any(
                marker in lower
                for marker in (
                    "mat-column-status",
                    "app-tracking-results",
                    "tbl-header-status",
                )
            ):
                return _html_has_tracking_status(html, tracking_number)
    except Exception as exc:
        logger.debug("Estes HTTP probe error for %s: %s", tracking_number, exc)
    return False


HTTP_PROBE_FUNCTIONS = {
    "usps": _probe_usps_http,
    "ups": _probe_ups_http,
    "estes": _probe_estes_http,
}


async def _probe_usps_browser(page, tracking_number: str) -> bool:
    url = get_tracking_url("usps", tracking_number)
    logger.debug("Probing USPS link: %s", url)

    try:
        await goto_tracking_page(page, url, timeout_ms=PROBE_TIMEOUT_MS)
        try:
            await page.wait_for_selector(
                ".tb-status, .tracking-progress-bar-status-container",
                timeout=8000,
            )
        except Exception:
            return False

        status = page.locator(".tb-status").first
        if await status.count() > 0:
            text = (await status.text_content() or "").strip().lower()
            if text and text not in {"status not available", "not available"}:
                logger.info("USPS link matched for %s", tracking_number)
                return True

        if await page.locator(".tracking-progress-bar-status-container").count() > 0:
            logger.info("USPS link matched for %s", tracking_number)
            return True

        return False
    except Exception as exc:
        logger.debug("USPS browser probe error for %s: %s", tracking_number, exc)
        return False


async def _probe_ups_browser(page, tracking_number: str) -> bool:
    from scrapers.navigation import dismiss_ups_overlays, wait_for_ups_tracking

    url = get_tracking_url("ups", tracking_number)
    logger.debug("Probing UPS link: %s", url)

    try:
        await goto_tracking_page(page, url, timeout_ms=PROBE_TIMEOUT_MS)
        await dismiss_ups_overlays(page)

        if not await wait_for_ups_tracking(page, tracking_number, timeout_ms=20000):
            return False

        logger.info("UPS link matched for %s", tracking_number)
        return True
    except Exception as exc:
        logger.debug("UPS browser probe error for %s: %s", tracking_number, exc)
        return False


async def _probe_estes_browser(page, tracking_number: str) -> bool:
    url = get_tracking_url("estes", tracking_number)
    logger.debug("Probing Estes link: %s", url)

    try:
        await goto_tracking_page(page, url, timeout_ms=PROBE_TIMEOUT_MS)
        await prepare_estes_tracking_page(page, tracking_number)
        state = await wait_for_estes_tracking(page, timeout_ms=15000)
        if state == "ready":
            logger.info("Estes link matched for %s", tracking_number)
            return True
        return False
    except Exception as exc:
        logger.debug("Estes browser probe error for %s: %s", tracking_number, exc)
        return False


BROWSER_PROBE_FUNCTIONS = {
    "usps": _probe_usps_browser,
    "ups": _probe_ups_browser,
    "estes": _probe_estes_browser,
}


async def _race_carrier_tasks(tasks: dict[asyncio.Task, CarrierType]) -> CarrierType | None:
    """Return the carrier for the first probe task that reports a match."""
    pending = set(tasks)
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                carrier = tasks[task]
                try:
                    matched = task.result()
                except asyncio.CancelledError:
                    continue
                except Exception as exc:
                    logger.warning("Link probe task failed for %s: %s", carrier, exc)
                    continue

                if matched:
                    return carrier
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    return None


async def _probe_via_http(tracking_number: str) -> CarrierType | None:
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SEC)
    async with aiohttp.ClientSession(headers=HTTP_HEADERS, timeout=timeout) as session:
        async def check(carrier: CarrierType) -> bool:
            probe_fn = HTTP_PROBE_FUNCTIONS.get(carrier)
            if not probe_fn:
                return False
            return await probe_fn(session, tracking_number)

        tasks = {
            asyncio.create_task(check(carrier)): carrier
            for carrier in IMPLEMENTED_CARRIERS
        }
        return await _race_carrier_tasks(tasks)


async def _probe_via_browser(tracking_number: str) -> CarrierType | None:
    async def check(carrier: CarrierType) -> bool:
        probe_fn = BROWSER_PROBE_FUNCTIONS.get(carrier)
        if not probe_fn:
            return False
        async with get_page(timeout_ms=PROBE_TIMEOUT_MS + 3000) as page:
            return await probe_fn(page, tracking_number)

    tasks = {
        asyncio.create_task(check(carrier)): carrier
        for carrier in IMPLEMENTED_CARRIERS
    }
    return await _race_carrier_tasks(tasks)


async def probe_carrier(tracking_number: str) -> CarrierType | None:
    """Detect carrier by probing carrier tracking links."""
    carrier, _method = await probe_carrier_with_method(tracking_number)
    return carrier


async def probe_carrier_with_method(
    tracking_number: str,
) -> tuple[CarrierType | None, str]:
    """
    Detect carrier and return how it was detected.

    Returns:
        (carrier, method) where method is link_probe, format_inference, or "".
    """
    normalized = normalize_tracking_number(tracking_number)
    if not normalized:
        logger.warning("Empty tracking number provided to probe_carrier")
        return None, ""

    logger.info("Probing carrier links for tracking number: %s", normalized)

    carrier = await _probe_via_http(normalized)
    if carrier:
        logger.info("Carrier detected via HTTP link probe: %s for %s", carrier, normalized)
        return carrier, "link_probe"

    carrier = await _probe_via_browser(normalized)
    if carrier:
        logger.info("Carrier detected via browser link probe: %s for %s", carrier, normalized)
        return carrier, "link_probe"

    carrier = infer_carrier_from_format(normalized)
    if carrier and carrier in IMPLEMENTED_CARRIERS:
        logger.warning(
            "Link probing blocked or inconclusive for %s; using format inference: %s",
            normalized,
            carrier,
        )
        return carrier, "format_inference"

    logger.warning("No carrier link matched for tracking number: %s", normalized)
    return None, ""


async def probe_carrier_result(tracking_number: str) -> dict:
    """Probe carrier links and return carrier info plus tracking URL."""
    normalized = normalize_tracking_number(tracking_number)
    if not normalized:
        return {"error": "Invalid tracking number", "tracking_number": tracking_number}

    carrier, detected_via = await probe_carrier_with_method(normalized)
    if not carrier:
        return {
            "error": (
                "Could not determine carrier for this tracking number. "
                "Check the number and try again."
            ),
            "tracking_number": normalized,
        }

    return {
        "carrier": carrier,
        "tracking_number": normalized,
        "tracking_url": get_tracking_url(carrier, normalized),
        "detected_via": detected_via,
    }
