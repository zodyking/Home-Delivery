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
            _browser = await _playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
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
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        context.set_default_timeout(timeout_ms)

        page = await context.new_page()
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
