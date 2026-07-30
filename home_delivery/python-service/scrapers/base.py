"""
Shared Playwright browser pool for scraping.
"""
from __future__ import annotations

import asyncio
import logging
import platform
import re
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)

# Global browser instance (lazy initialized)
_browser: Browser | None = None
_playwright = None
_lock = asyncio.Lock()


def _chrome_major(version: str) -> str:
    match = re.match(r"(\d+)", version or "")
    return match.group(1) if match else "143"


def _browser_identity(browser: Browser) -> dict[str, str]:
    """
    Build UA / Client Hints that match the real Playwright Chromium build.

    Spoofing a newer Chrome (e.g. 150) while running Chromium 143 trips
    Akamai on tools.usps.com, especially from Linux addon containers.
    """
    version = getattr(browser, "version", "") or "143.0.0.0"
    major = _chrome_major(version)
    # Chrome stable UAs use MAJOR.0.0.0 — a full Chromium build like
    # 143.0.7499.4 makes UPS drop trackdetails and never render history.
    ua_version = f"{major}.0.0.0"
    system = platform.system()

    if system == "Linux":
        ua = (
            f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{ua_version} Safari/537.36"
        )
        ch_platform = '"Linux"'
        ua_platform = "Linux"
        platform_version = "6.5.0"
    elif system == "Darwin":
        ua = (
            f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{ua_version} Safari/537.36"
        )
        ch_platform = '"macOS"'
        ua_platform = "macOS"
        platform_version = "14.0.0"
    else:
        ua = (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{ua_version} Safari/537.36"
        )
        ch_platform = '"Windows"'
        ua_platform = "Windows"
        platform_version = "15.0.0"

    sec_ch_ua = (
        f'"Not;A=Brand";v="8", "Chromium";v="{major}", "Google Chrome";v="{major}"'
    )
    return {
        "user_agent": ua,
        "sec_ch_ua": sec_ch_ua,
        "sec_ch_ua_platform": ch_platform,
        "ua_platform": ua_platform,
        "platform_version": platform_version,
        "full_version": ua_version,
        "major": major,
    }


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
            logger.info("Browser launched successfully (%s)", _browser.version)

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


def _stealth_init_script(identity: dict[str, str]) -> str:
    major = identity["major"]
    full = identity["full_version"]
    ua_platform = identity["ua_platform"]
    platform_version = identity["platform_version"]
    return f"""
        Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
        Object.defineProperty(navigator, 'languages', {{ get: () => ['en-US', 'en'] }});
        window.chrome = {{ runtime: {{}}, loadTimes: () => ({{}}) }};

        const originalQuery = window.navigator.permissions?.query;
        if (originalQuery) {{
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications'
                    ? Promise.resolve({{ state: 'denied', onchange: null }})
                    : originalQuery(parameters)
            );
        }}

        Object.defineProperty(navigator, 'userAgentData', {{
            get: () => ({{
                brands: [
                    {{ brand: 'Not;A=Brand', version: '8' }},
                    {{ brand: 'Chromium', version: '{major}' }},
                    {{ brand: 'Google Chrome', version: '{major}' }},
                ],
                mobile: false,
                platform: '{ua_platform}',
                getHighEntropyValues: () => Promise.resolve({{
                    architecture: 'x86',
                    bitness: '64',
                    fullVersionList: [
                        {{ brand: 'Not;A=Brand', version: '8.0.0.0' }},
                        {{ brand: 'Chromium', version: '{full}' }},
                        {{ brand: 'Google Chrome', version: '{full}' }},
                    ],
                    model: '',
                    platform: '{ua_platform}',
                    platformVersion: '{platform_version}',
                    uaFullVersion: '{full}',
                }}),
            }}),
        }});

        Object.defineProperty(navigator, 'plugins', {{
            get: () => [
                {{ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' }},
                {{ name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' }},
                {{ name: 'Native Client', filename: 'internal-nacl-plugin' }},
            ],
        }});
    """


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
    identity = _browser_identity(browser)
    context: BrowserContext | None = None
    page: Page | None = None

    try:
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            user_agent=identity["user_agent"],
            # Only Client Hints + language. Do NOT set Accept / Sec-Fetch-* /
            # Upgrade-Insecure-Requests here — context headers apply to every
            # request, including UPS Track/GetStatus XHR, and break the API.
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Ch-Ua": identity["sec_ch_ua"],
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": identity["sec_ch_ua_platform"],
            },
            ignore_https_errors=True,
        )
        context.set_default_timeout(timeout_ms)

        page = await context.new_page()
        await page.add_init_script(_stealth_init_script(identity))
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
