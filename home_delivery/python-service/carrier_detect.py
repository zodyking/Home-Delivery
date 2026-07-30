"""
Carrier detection and tracking URL generation.
Detects USPS, UPS, and FedEx tracking numbers using regex patterns.
"""
from __future__ import annotations

import re
from typing import Literal

CarrierType = Literal["usps", "ups", "fedex"]

# Tracking number patterns (compiled for performance)
PATTERNS: dict[CarrierType, list[re.Pattern]] = {
    "ups": [
        # Standard UPS: 1Z followed by 16 alphanumeric characters
        re.compile(r"^1Z[A-Z0-9]{16}$", re.IGNORECASE),
        # UPS Mail Innovations: starts with MI or ends with specific patterns
        re.compile(r"^(MI|9[2-4]\d{20,26})$", re.IGNORECASE),
    ],
    "usps": [
        # USPS 20-digit (older format)
        re.compile(r"^\d{20}$"),
        # USPS 22-digit with service type prefix (9400, 9205, 9407, etc.)
        re.compile(r"^9[2-5]\d{19,25}$"),
        # USPS with routing barcode (420 prefix + zip + tracking)
        re.compile(r"^420\d{5}9[2-5]\d{19,21}$"),
        # USPS International (EA, EC, CP, RA, RB, RC, etc. + 9 digits + US)
        re.compile(r"^[A-Z]{2}\d{9}US$", re.IGNORECASE),
        # USPS Certified Mail
        re.compile(r"^9407\d{16,18}$"),
        # USPS Priority Mail Express
        re.compile(r"^(EA|EC|CP)\d{9}US$", re.IGNORECASE),
    ],
    "fedex": [
        # FedEx Express: 12 digits
        re.compile(r"^\d{12}$"),
        # FedEx Express: 15 digits
        re.compile(r"^\d{15}$"),
        # FedEx Ground: 15 digits starting with specific prefixes
        re.compile(r"^(96|98)\d{18,20}$"),
        # FedEx Ground: 22 digits
        re.compile(r"^\d{22}$"),
        # FedEx SmartPost (may transition to USPS)
        re.compile(r"^61\d{18,20}$"),
    ],
}

# Tracking URL templates
TRACKING_URLS: dict[CarrierType, str] = {
    "usps": "https://tools.usps.com/tracking/{tracking_number}",
    "ups": "https://www.ups.com/track?tracknum={tracking_number}&loc=en_US&requester=ST",
    "fedex": "https://www.fedex.com/fedextrack/?trknbr={tracking_number}",
}


def detect_carrier(tracking_number: str) -> CarrierType | None:
    """
    Detect the carrier from a tracking number.

    Args:
        tracking_number: The tracking number to analyze.

    Returns:
        The carrier type ('usps', 'ups', or 'fedex') or None if not detected.
    """
    # Normalize: remove spaces, dashes, uppercase
    cleaned = re.sub(r"[\s\-]", "", tracking_number.strip().upper())

    if not cleaned:
        return None

    # Check UPS first (most distinctive pattern with 1Z prefix)
    for pattern in PATTERNS["ups"]:
        if pattern.match(cleaned):
            return "ups"

    # Check FedEx (all numeric patterns that don't match USPS)
    for pattern in PATTERNS["fedex"]:
        if pattern.match(cleaned):
            # FedEx 12/15 digit could conflict with some patterns
            # but FedEx patterns are distinct enough
            return "fedex"

    # Check USPS (most common for e-commerce)
    for pattern in PATTERNS["usps"]:
        if pattern.match(cleaned):
            return "usps"

    return None


def get_tracking_url(carrier: CarrierType, tracking_number: str) -> str:
    """
    Get the tracking URL for a carrier and tracking number.

    Args:
        carrier: The carrier type.
        tracking_number: The tracking number.

    Returns:
        The full tracking URL.
    """
    template = TRACKING_URLS.get(carrier)
    if not template:
        return ""

    cleaned = re.sub(r"[\s\-]", "", tracking_number.strip().upper())
    return template.format(tracking_number=cleaned)


def normalize_tracking_number(tracking_number: str) -> str:
    """
    Normalize a tracking number by removing spaces and dashes, uppercasing.

    Args:
        tracking_number: The raw tracking number.

    Returns:
        The normalized tracking number.
    """
    return re.sub(r"[\s\-]", "", tracking_number.strip().upper())


def validate_tracking_number(tracking_number: str, carrier: CarrierType | None = None) -> bool:
    """
    Validate a tracking number against known patterns.

    Args:
        tracking_number: The tracking number to validate.
        carrier: Optional carrier to validate against specifically.

    Returns:
        True if the tracking number is valid.
    """
    if carrier:
        cleaned = normalize_tracking_number(tracking_number)
        patterns = PATTERNS.get(carrier, [])
        return any(p.match(cleaned) for p in patterns)

    return detect_carrier(tracking_number) is not None
