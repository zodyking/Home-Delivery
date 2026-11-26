"""
FedEx tracking scraper — thin wrapper around unified fetch.

Page: https://www.fedex.com/fedextrack/?trknbr=<tracking number>
Scan history is behind the "View more details" control.
"""
from __future__ import annotations

from typing import Any

from .tracking_fetch import fetch_carrier_tracking


async def scrape_fedex(tracking_number: str) -> dict[str, Any]:
    """Scrape FedEx tracking for status and travel-history events."""
    return await fetch_carrier_tracking("fedex", tracking_number)
