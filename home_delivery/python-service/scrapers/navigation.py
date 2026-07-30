"""
Reliable Playwright navigation for carrier tracking pages.

Handles Akamai challenges, HTTP/2 errors, and ensures tracking content loads
before parsing.
"""
from __future__ import annotations

import asyncio
import logging
import time

from playwright.async_api import Page

logger = logging.getLogger(__name__)

HTTP2_ERROR_MARKERS = (
    "ERR_HTTP2_PROTOCOL_ERROR",
    "ERR_HTTP2",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_NETWORK_CHANGED",
)

# Akamai challenge indicators
AKAMAI_MARKERS = (
    "bm-verify",
    "interstitialchallenge",
    "/_sec/verify",
    "akamaihd.net",
)


async def goto_tracking_page(page: Page, url: str, timeout_ms: int = 45000) -> None:
    """
    Navigate to a carrier tracking URL with retries for flaky HTTP/2 responses.

    Sets appropriate referer and Sec-Fetch-Site headers based on the carrier domain.
    """
    # Referer only — never set Sec-Fetch-* on the page context (applies to XHR
    # too and breaks UPS; for USPS a cold "same-site" claim without warm-up
    # also trips Akamai).
    if "usps.com" in url:
        await page.set_extra_http_headers({
            "Referer": "https://www.usps.com/",
        })
    elif "ups.com" in url:
        await page.set_extra_http_headers({
            "Referer": "https://www.ups.com/",
        })
    elif "fedex.com" in url:
        await page.set_extra_http_headers({
            "Referer": "https://www.fedex.com/",
        })
    elif "estes-express.com" in url:
        await page.set_extra_http_headers({
            "Referer": "https://www.estes-express.com/",
        })

    last_error: Exception | None = None

    for attempt, wait_until in enumerate(("domcontentloaded", "commit", "load")):
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return
        except Exception as exc:
            last_error = exc
            message = str(exc)
            if any(marker in message for marker in HTTP2_ERROR_MARKERS):
                logger.warning(
                    "HTTP/2 navigation failed for %s (attempt %s, wait=%s): %s",
                    url,
                    attempt + 1,
                    wait_until,
                    exc,
                )
                await asyncio.sleep(1.5)
                continue
            raise

    if last_error:
        raise last_error


def _content_has_akamai_challenge(content: str) -> bool:
    """Check if page content indicates an Akamai challenge."""
    lower = content.lower()
    return any(marker in lower for marker in AKAMAI_MARKERS)


async def dismiss_usps_overlays(page: Page) -> None:
    """Accept cookie / privacy banners that can block tracking UI."""
    for selector in (
        "#btn-agree",
        "button:has-text('Accept All Cookies')",
        "button:has-text('Accept Cookies')",
        "button:has-text('I Accept')",
        "#onetrust-accept-btn-handler",
        "button:has-text('Agree and Continue')",
    ):
        loc = page.locator(selector)
        try:
            if await loc.count() == 0:
                continue
            await loc.first.click(timeout=1500, force=True)
            await page.wait_for_timeout(400)
        except Exception:
            continue


async def warmup_usps_session(page: Page) -> None:
    """
    Visit usps.com first so tools.usps.com navigations look same-site and
    Akamai can mint cookies before the tracking deep-link.
    """
    try:
        await page.goto(
            "https://www.usps.com/",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        await dismiss_usps_overlays(page)
        await page.wait_for_timeout(1200)
    except Exception as exc:
        logger.warning("USPS warm-up visit failed (continuing): %s", exc)


async def wait_for_usps_tracking(page: Page, timeout_ms: int = 60000) -> bool:
    """
    Wait for USPS tracking DOM or detect an Akamai block.

    Polls for up to timeout_ms, waiting longer if an Akamai interstitial is
    detected (it may resolve on its own via sensor scripts + reload).
    """
    deadline = time.monotonic() + (timeout_ms / 1000)
    akamai_wait_extended = False
    access_denied_retries = 0

    while time.monotonic() < deadline:
        title = (await page.title()).lower()

        # Check for tracking content first (challenge HTML can linger in scripts)
        tracking_found = await page.locator(
            ".tb-status, .tracking-progress-bar-status-container, "
            "#trackingNum, .tracking-number"
        ).count() > 0

        if tracking_found and "access denied" not in title:
            # Prefer real status text when available
            if await page.locator(".tb-status").count() > 0:
                return True
            # Tracking number alone is enough to attempt parse
            if await page.locator("#trackingNum, .tracking-number").count() > 0:
                # Give SPA a beat to paint status
                await page.wait_for_timeout(1500)
                if await page.locator(".tb-status").count() > 0:
                    return True
                return True

        content = await page.content()
        lower_content = content.lower()

        # Hard block — Access Denied page
        if "access denied" in title or (
            "access denied" in lower_content and "tb-status" not in lower_content
        ):
            access_denied_retries += 1
            if access_denied_retries <= 2:
                logger.info(
                    "USPS Access Denied detected (attempt %s), reloading after pause",
                    access_denied_retries,
                )
                await page.wait_for_timeout(4000 + access_denied_retries * 2000)
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                continue
            return False

        # Akamai challenge interstitial — wait for sensor JS to clear + reload
        if _content_has_akamai_challenge(content) and "tb-status" not in lower_content:
            if not akamai_wait_extended:
                logger.info("USPS Akamai challenge detected, extending wait by 45s")
                akamai_wait_extended = True
                deadline = max(deadline, time.monotonic() + 45)
            await page.wait_for_timeout(2500)
            continue

        await dismiss_usps_overlays(page)
        await page.wait_for_timeout(750)

    # Final check
    return await page.locator(".tb-status, #trackingNum").count() > 0


async def wait_for_ups_tracking(
    page: Page,
    tracking_number: str,
    timeout_ms: int = 45000,
) -> bool:
    """
    Wait until the UPS tracking results UI is actually ready.

    Requires real result chrome (Show/Hide Details, shipment progress, or
    stApp result nodes) — not merely the tracking number in HTML/source/nav.
    """
    _ = tracking_number  # call-site compatibility; readiness is DOM-based
    ready_js = """() => {
        if (document.getElementById("shipProg_act_Date0")) return true;
        if (document.querySelector("ups-shipment-progress")) return true;
        if (document.getElementById("stApp_copytrackingnumber")) return true;
        const buttons = Array.from(
            document.querySelectorAll("button, a, [role='button']")
        );
        return buttons.some((el) => {
            const text = (el.innerText || el.textContent || "").replace(/\\s+/g, " ");
            return /\\b(Show Details|Hide Details)\\b/i.test(text);
        });
    }"""

    try:
        await page.wait_for_function(ready_js, timeout=timeout_ms)
        return True
    except Exception:
        pass

    selectors = (
        "#shipProg_act_Date0",
        "ups-shipment-progress",
        "#stApp_copytrackingnumber",
        "button:has-text('Show Details')",
        "button:has-text('Hide Details')",
    )
    for selector in selectors:
        try:
            await page.wait_for_selector(selector, timeout=2500)
            return True
        except Exception:
            continue

    return False


async def wait_for_fedex_tracking(page: Page, timeout_ms: int = 25000) -> bool:
    """Wait for FedEx tracking UI elements."""
    try:
        await page.wait_for_selector(
            "[class*='shipmentStatus'], [class*='tracking-status'], [class*='travel-history']",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def wait_for_estes_tracking(page: Page, timeout_ms: int = 45000) -> str:
    """
    Wait for Estes tracking results or a not-found error.

    Returns:
        "ready" when results table/status is present,
        "not_found" when the red unavailable banner appears,
        "timeout" if neither appears in time.
    """
    deadline = time.monotonic() + (timeout_ms / 1000)
    not_found_re = "not found or tracking information unavailable"

    while time.monotonic() < deadline:
        content = (await page.content()).lower()
        if not_found_re in content:
            return "not_found"

        ready = await page.locator(
            "app-tracking-results .mat-column-status, "
            "app-tracking-results .mat-mdc-row, "
            "td.mat-column-status, "
            ".tbl-header-status"
        ).count() > 0
        if ready:
            return "ready"

        await page.wait_for_timeout(500)

    content = (await page.content()).lower()
    if not_found_re in content:
        return "not_found"
    return "timeout"
