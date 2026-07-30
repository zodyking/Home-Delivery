"""
Carrier tracking URL generation.

Carrier detection is done by probing these URLs (see carrier_probe.py),
not by hard-coded tracking-number patterns.
"""
from __future__ import annotations

import re
from typing import Literal

CarrierType = Literal["usps", "ups", "fedex", "estes"]

# Full carrier list (FedEx reserved until implemented).
CARRIERS: list[CarrierType] = ["estes", "ups", "fedex", "usps"]
# Scrapers that are actually implemented today.
IMPLEMENTED_CARRIERS: list[CarrierType] = ["estes", "ups", "usps"]
# Ambiguous auto-detect order: Estes → UPS → USPS last (Akamai-fragile).
AUTO_DETECT_ORDER: list[CarrierType] = ["estes", "ups", "usps"]

# Tracking URL templates (user-provided link logic)
TRACKING_URLS: dict[CarrierType, str] = {
    "usps": "https://tools.usps.com/tracking/{tracking_number}",
    "ups": "https://www.ups.com/track?tracknum={tracking_number}&loc=en_US&requester=ST/trackdetails",
    "fedex": "https://www.fedex.com/fedextrack/?trknbr={tracking_number}",
    "estes": "https://www.estes-express.com/myestes/shipment-tracking/?type=PRO&query={tracking_number}",
}


def normalize_tracking_number(tracking_number: str) -> str:
    """Remove spaces/dashes and uppercase."""
    return re.sub(r"[\s\-]", "", tracking_number.strip().upper())


def format_tracking_for_url(carrier: CarrierType, tracking_number: str) -> str:
    """Normalize a tracking number for embedding in a carrier URL."""
    cleaned = normalize_tracking_number(tracking_number)
    if carrier == "estes" and re.fullmatch(r"\d{10}", cleaned):
        # Estes UI / deep-links commonly use XXX-XXXXXXX PRO formatting.
        return f"{cleaned[:3]}-{cleaned[3:]}"
    return cleaned


def get_tracking_url(carrier: CarrierType, tracking_number: str) -> str:
    """Build the carrier tracking page URL for a tracking number."""
    template = TRACKING_URLS.get(carrier)
    if not template:
        return ""

    return template.format(tracking_number=format_tracking_for_url(carrier, tracking_number))


def get_all_tracking_urls(tracking_number: str) -> dict[CarrierType, str]:
    """Return every carrier tracking URL for a number (used during probing)."""
    return {
        carrier: template.format(
            tracking_number=format_tracking_for_url(carrier, tracking_number)
        )
        for carrier, template in TRACKING_URLS.items()
    }


def infer_carrier_from_format(tracking_number: str) -> CarrierType | None:
    """
    Infer carrier from tracking-number shape when link probing is blocked.

    Used only as a last resort after HTTP and browser probes fail (e.g. Akamai).
    """
    cleaned = normalize_tracking_number(tracking_number)
    if not cleaned:
        return None

    # UPS: 1Z + 16 alphanumeric (very reliable).
    if re.fullmatch(r"1Z[A-Z0-9]{16}", cleaned):
        return "ups"

    # Estes PRO: typically 10 digits (often shown as XXX-XXXXXXX).
    if re.fullmatch(r"\d{10}", cleaned):
        return "estes"

    # FedEx: common numeric lengths.
    if re.fullmatch(r"\d{12}", cleaned) or re.fullmatch(r"\d{15}", cleaned):
        return "fedex"

    # USPS: 20–34 digit Intelligent Mail / IMpb barcodes (Informed Delivery scans).
    if re.fullmatch(r"\d{20,34}", cleaned):
        return "usps"

    # USPS: Priority / Certified / other letter+suffixed formats.
    if re.fullmatch(r"[A-Z]{2}\d{9}US", cleaned):
        return "usps"
    if re.fullmatch(r"\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{2}", cleaned.replace(" ", "")):
        return "usps"

    # Generic alphanumeric blocks often used by USPS (e.g. 9400… style without strict length).
    if re.fullmatch(r"\d{13,34}", cleaned) and cleaned.startswith(("9", "8", "7")):
        return "usps"

    return None


def auto_detect_carrier_order(tracking_number: str) -> list[CarrierType]:
    """
    Carriers to try for scrape-first auto-detect, in order.

    Unambiguous formats (1Z UPS, 10-digit Estes PRO, long USPS IMpb) only
    try their matching carrier — never fall through to an unrelated carrier
    and surface e.g. "Estes tracking content not found" for a UPS number.

    Ambiguous numbers use Estes → UPS → USPS (FedEx skipped until implemented).
    """
    cleaned = normalize_tracking_number(tracking_number)
    hinted = infer_carrier_from_format(cleaned)

    if hinted == "ups":
        return ["ups"]
    if hinted == "estes":
        return ["estes"]
    if hinted == "usps":
        return ["usps"]
    # FedEx not implemented — fall through to ambiguous order rather than
    # returning a stub-only list.
    if hinted == "fedex":
        return list(AUTO_DETECT_ORDER)

    return list(AUTO_DETECT_ORDER)
