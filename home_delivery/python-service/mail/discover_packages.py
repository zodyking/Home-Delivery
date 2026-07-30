"""
Auto-discover packages from Informed Delivery tracking numbers.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from carrier_detect import get_tracking_url, infer_carrier_from_format, normalize_tracking_number
from config_store import config_store

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def add_discovered_packages(
    tracking_numbers: list[str],
    *,
    account: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Add Informed Delivery tracking numbers to the dashboard if missing.

    Packages are marked auto_discovered / needs_details so the UI can prompt
    for recipient + destination.
    """
    if not tracking_numbers:
        return []

    existing = await config_store.get_packages()
    known = {
        normalize_tracking_number(pkg.get("tracking_number", ""))
        for pkg in existing
        if pkg.get("tracking_number")
    }

    created: list[dict[str, Any]] = []
    account_id = (account or {}).get("id")
    account_label = (account or {}).get("label") or ""

    for raw in tracking_numbers:
        normalized = normalize_tracking_number(raw)
        if not normalized or normalized in known:
            continue

        carrier = infer_carrier_from_format(normalized) or "usps"
        package = {
            "id": str(uuid.uuid4()),
            "tracking_number": normalized,
            "carrier": carrier,
            "recipient": "",
            "destination": "",
            "destination_account_id": account_id,
            "tracking_url": get_tracking_url(carrier, normalized),
            "status": "Pending",
            "status_detail": "Discovered from USPS Informed Delivery",
            "events": [],
            "last_event_fingerprint": None,
            "out_for_delivery": False,
            "delivered": False,
            "created_at": _utc_now_iso(),
            "last_polled": None,
            "next_poll_at": _utc_now_iso(),
            "poll_interval_seconds": 3600,
            "error": None,
            "auto_discovered": True,
            "needs_details": True,
            "source": "informed_delivery",
            "source_account_id": account_id,
            "source_account_label": account_label,
        }

        saved = await config_store.add_package(package)
        known.add(normalized)
        created.append(saved)
        logger.info(
            "Auto-discovered package %s (%s) from Informed Delivery account %s",
            normalized,
            carrier,
            account_label or account_id or "unknown",
        )

    return created
