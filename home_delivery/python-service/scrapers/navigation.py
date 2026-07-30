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
    # Set referer and Sec-Fetch-Site based on carrier domain
    if "usps.com" in url:
        await page.set_extra_http_headers({
            "Referer": "https://www.usps.com/",
            "Sec-Fetch-Site": "same-site",
        })
    elif "ups.com" in url:
        # Referer only — never set Sec-Fetch-* on the page (applies to XHR too).
        await page.set_extra_http_headers({
            "Referer": "https://www.ups.com/",
        })
    elif "fedex.com" in url:
        await page.set_extra_http_headers({
            "Referer": "https://www.fedex.com/",
            "Sec-Fetch-Site": "same-origin",
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


async def wait_for_usps_tracking(page: Page, timeout_ms: int = 45000) -> bool:
    """
    Wait for USPS tracking DOM or detect an Akamai block.

    Polls for up to timeout_ms, waiting longer if an Akamai interstitial is
    detected (it may resolve on its own).
    """
    deadline = time.monotonic() + (timeout_ms / 1000)
    akamai_wait_extended = False

    while time.monotonic() < deadline:
        title = (await page.title()).lower()

        # Hard block — Access Denied page
        if "access denied" in title:
            # Give it a bit more time in case it's a temporary interstitial
            if not akamai_wait_extended:
                logger.info("USPS Access Denied title detected, waiting 5s for resolution")
                akamai_wait_extended = True
                await page.wait_for_timeout(5000)
                continue
            return False

        # Check for tracking content
        tracking_found = await page.locator(
            ".tb-status, .tracking-progress-bar-status-container"
        ).count() > 0

        if tracking_found:
            return True

        content = await page.content()
        lower_content = content.lower()

        # Hard block in body
        if "access denied" in lower_content and "tb-status" not in lower_content:
            if not akamai_wait_extended:
                logger.info("USPS Access Denied in content, waiting 5s")
                akamai_wait_extended = True
                await page.wait_for_timeout(5000)
                continue
            return False

        # Akamai challenge interstitial — wait longer
        if _content_has_akamai_challenge(content):
            if not akamai_wait_extended:
                logger.info("USPS Akamai challenge detected, extending wait by 10s")
                akamai_wait_extended = True
                # Extend deadline for Akamai challenge resolution
                deadline = max(deadline, time.monotonic() + 10)
            await page.wait_for_timeout(2000)
            continue

        await page.wait_for_timeout(750)

    # Final check
    return await page.locator(".tb-status").count() > 0


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
