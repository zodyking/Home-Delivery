"""
Carrier tracking URL generation.

Carrier detection is done by probing these URLs (see carrier_probe.py),
not by hard-coded tracking-number patterns.
"""
from __future__ import annotations

import re
from typing import Literal

CarrierType = Literal["usps", "ups", "fedex"]

CARRIERS: list[CarrierType] = ["usps", "ups", "fedex"]

# Tracking URL templates (user-provided link logic)
TRACKING_URLS: dict[CarrierType, str] = {
    "usps": "https://tools.usps.com/tracking/{tracking_number}",
    "ups": "https://www.ups.com/track?tracknum={tracking_number}",
    "fedex": "https://www.fedex.com/fedextrack/?trknbr={tracking_number}",
}


def normalize_tracking_number(tracking_number: str) -> str:
    """Remove spaces/dashes and uppercase."""
    return re.sub(r"[\s\-]", "", tracking_number.strip().upper())


def get_tracking_url(carrier: CarrierType, tracking_number: str) -> str:
    """Build the carrier tracking page URL for a tracking number."""
    template = TRACKING_URLS.get(carrier)
    if not template:
        return ""

    cleaned = normalize_tracking_number(tracking_number)
    return template.format(tracking_number=cleaned)


def get_all_tracking_urls(tracking_number: str) -> dict[CarrierType, str]:
    """Return every carrier tracking URL for a number (used during probing)."""
    cleaned = normalize_tracking_number(tracking_number)
    return {carrier: url.format(tracking_number=cleaned) for carrier, url in TRACKING_URLS.items()}
