"""
USPS tracking scraper — thin wrapper around unified fetch.
"""
from __future__ import annotations

from typing import Any

from .tracking_fetch import fetch_carrier_tracking


async def scrape_usps(tracking_number: str) -> dict[str, Any]:
    """Scrape USPS tracking page for package status and event history."""
    return await fetch_carrier_tracking("usps", tracking_number)
