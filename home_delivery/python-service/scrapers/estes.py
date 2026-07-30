"""
Estes Express tracking scraper — thin wrapper around unified fetch.
"""
from __future__ import annotations

from typing import Any

from .tracking_fetch import fetch_carrier_tracking


async def scrape_estes(tracking_number: str) -> dict[str, Any]:
    """Scrape Estes My Estes shipment tracking for status and history."""
    return await fetch_carrier_tracking("estes", tracking_number)
