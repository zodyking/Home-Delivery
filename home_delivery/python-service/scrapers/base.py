"""
Shared Playwright browser pool for scraping.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)

# Global browser instance (lazy initialized)
_browser: Browser | None = None
_playwright = None
_lock = asyncio.Lock()


async def _get_browser() -> Browser:
    """Get or create the shared browser instance."""
    global _browser, _playwright

    async with _lock:
        if _browser is None or not _browser.is_connected():
            logger.info("Launching Chromium browser...")
            _playwright = await async_playwright().start()
            # NOTE: Do not pass --disable-http2. UPS Track/GetStatus XHR hangs
            # without HTTP/2 even though document navigation may still work.
            _browser = await _playwright.chromium.launch(
                headless=True,
                args=[
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            logger.info("Browser launched successfully")

        return _browser


async def close_browser() -> None:
    """Close the shared browser instance."""
    global _browser, _playwright

    async with _lock:
        if _browser:
            await _browser.close()
            _browser = None
        if _playwright:
            await _playwright.stop()
            _playwright = None


async def reset_browser_on_failure() -> None:
    """Reset the shared browser after a transport-level navigation failure."""
    await close_browser()


@asynccontextmanager
async def get_page(timeout_ms: int = 30000) -> AsyncGenerator[Page, None]:
    """
    Get a new page in a fresh browser context.

    Args:
        timeout_ms: Default timeout for page operations.

    Yields:
        A new Page instance.
    """
    browser = await _get_browser()
    context: BrowserContext | None = None
    page: Page | None = None

    try:
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
            # Only Client Hints + language. Do NOT set Accept / Sec-Fetch-* /
            # Upgrade-Insecure-Requests here — context headers apply to every
            # request, including UPS Track/GetStatus XHR, and break the API.
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Ch-Ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
            },
            ignore_https_errors=True,
        )
        context.set_default_timeout(timeout_ms)

        page = await context.new_page()
        await page.add_init_script(
            """
            // Hide webdriver flag
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

            // Mimic Chrome 150 runtime
            window.chrome = { runtime: {}, loadTimes: () => ({}) };

            // Override permissions query to appear normal
            const originalQuery = window.navigator.permissions?.query;
            if (originalQuery) {
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications'
                        ? Promise.resolve({ state: 'denied', onchange: null })
                        : originalQuery(parameters)
                );
            }

            // Set userAgentData for Chrome Client Hints API
            Object.defineProperty(navigator, 'userAgentData', {
                get: () => ({
                    brands: [
                        { brand: 'Not;A=Brand', version: '8' },
                        { brand: 'Chromium', version: '150' },
                        { brand: 'Google Chrome', version: '150' },
                    ],
                    mobile: false,
                    platform: 'Windows',
                    getHighEntropyValues: () => Promise.resolve({
                        architecture: 'x86',
                        bitness: '64',
                        fullVersionList: [
                            { brand: 'Not;A=Brand', version: '8.0.0.0' },
                            { brand: 'Chromium', version: '150.0.0.0' },
                            { brand: 'Google Chrome', version: '150.0.0.0' },
                        ],
                        model: '',
                        platform: 'Windows',
                        platformVersion: '10.0.0',
                        uaFullVersion: '150.0.0.0',
                    }),
                }),
            });

            // Hide automation-related properties
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin' },
                ],
            });
            """
        )
        yield page

    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if context:
            try:
                await context.close()
            except Exception:
                pass
