"""
Reliable Playwright navigation for carrier tracking pages.
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


async def goto_tracking_page(page: Page, url: str, timeout_ms: int = 45000) -> None:
    """
    Navigate to a carrier tracking URL with retries for flaky HTTP/2 responses.
    """
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


async def wait_for_usps_tracking(page: Page, timeout_ms: int = 35000) -> bool:
    """Wait for USPS tracking DOM or detect an Akamai block."""
    deadline = time.monotonic() + (timeout_ms / 1000)

    while time.monotonic() < deadline:
        title = (await page.title()).lower()
        if "access denied" in title:
            return False

        if await page.locator(".tb-status, .tracking-progress-bar-status-container").count() > 0:
            return True

        content = (await page.content()).lower()
        if "access denied" in content and "tb-status" not in content:
            return False
        if "bm-verify" in content and "tb-status" not in content:
            await page.wait_for_timeout(1500)
            continue

        await page.wait_for_timeout(750)

    return await page.locator(".tb-status").count() > 0


async def wait_for_ups_tracking(page: Page, tracking_number: str, timeout_ms: int = 25000) -> bool:
    """Wait for UPS tracking UI (headline or expanded timeline)."""
    selectors = (
        "#shipProg_act_Date0",
        "ups-shipment-progress",
        "text=Tracking Details",
    )
    tracking_upper = tracking_number.upper()

    try:
        await page.wait_for_function(
            """(tracking) => {
                const text = document.body?.innerText || "";
                if (text.toUpperCase().includes(tracking)) return true;
                return !!document.getElementById("shipProg_act_Date0")
                    || !!document.querySelector("ups-shipment-progress");
            }""",
            tracking_upper,
            timeout=timeout_ms,
        )
        return True
    except Exception:
        for selector in selectors:
            try:
                await page.wait_for_selector(selector, timeout=3000)
                return True
            except Exception:
                continue
    return False
