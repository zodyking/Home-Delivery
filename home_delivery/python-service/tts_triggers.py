"""
TTS trigger logic for package status changes and mail delivery.
Respects quiet hours, per-type toggles, media-player skip/volume, and message prefix.
"""
from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any

from config_store import config_store
from tts_engine import dispatch_tts

logger = logging.getLogger(__name__)

_CARRIER_NAMES = {
    "ups": "UPS",
    "usps": "USPS",
    "fedex": "FedEx",
    "estes": "Estes Express",
}


def _parse_time(t: str) -> time | None:
    """Parse HH:MM time string."""
    try:
        parts = t.split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return None


def _in_quiet_hours(start: str, end: str) -> bool:
    """Return True when announcements should be suppressed (outside allowed window)."""
    now = datetime.now().time()
    start_time = _parse_time(start)
    end_time = _parse_time(end)

    if not start_time or not end_time:
        return False

    if start_time <= end_time:
        return not (start_time <= now <= end_time)
    return end_time < now < start_time


async def _should_announce(event_type: str) -> bool:
    """Check if TTS should be triggered based on settings."""
    config = await config_store.load()
    tts = config.get("tts", {})

    if not tts.get("enabled"):
        return False

    toggle_map = {
        "status_change": "enable_status_change",
        "out_for_delivery": "enable_out_for_delivery",
        "delivered": "enable_delivered",
        "mail_arrived": "enable_mail_arrived",
    }

    toggle_key = toggle_map.get(event_type)
    if toggle_key and not tts.get(toggle_key, True):
        return False

    start = tts.get("start_time", "08:00")
    end = tts.get("end_time", "21:00")

    if _in_quiet_hours(start, end):
        logger.debug(f"TTS skipped: outside announcement hours ({start} - {end})")
        return False

    return True


def _format_carrier(carrier: str | None) -> str:
    key = (carrier or "").strip().lower()
    if key in _CARRIER_NAMES:
        return _CARRIER_NAMES[key]
    if carrier and carrier.strip():
        return carrier.strip().title()
    return "carrier"


def _format_status(status: str | None) -> str:
    text = (status or "updated").strip()
    return text.lower() if text else "updated"


def _package_phrase(
    carrier: str | None,
    recipient: str | None,
    destination: str | None,
) -> str:
    """Natural mid-sentence phrase, e.g. 'the UPS package for Mom'."""
    name = _format_carrier(carrier)
    recipient = (recipient or "").strip()
    destination = (destination or "").strip()

    if recipient and destination:
        return f"the {name} package for {recipient}, going to {destination}"
    if recipient:
        return f"the {name} package for {recipient}"
    if destination:
        return f"the {name} package going to {destination}"
    return f"your {name} package"


def _build_package_message(
    package: dict[str, Any],
    status_changed: bool = False,
    newly_ofd: bool = False,
    newly_delivered: bool = False,
) -> str:
    """Build a short, conversational TTS message for package status."""
    phrase = _package_phrase(
        package.get("carrier"),
        package.get("recipient"),
        package.get("destination"),
    )

    if newly_delivered:
        return f"{phrase} has been delivered."

    if newly_ofd:
        return f"{phrase} is out for delivery."

    if status_changed:
        status = _format_status(package.get("status"))
        events = package.get("events", [])
        location = ""
        if events:
            location = (events[0].get("location") or "").strip()

        msg = f"{phrase} is now {status}."
        if location:
            msg += f" It was last seen in {location}."
        return msg

    return f"{phrase} is {_format_status(package.get('status'))}."


def _account_label(account: dict[str, Any]) -> str:
    """Human-readable address name for TTS."""
    label = (account.get("label") or "").strip()
    if label:
        return label
    user = (account.get("imap_user") or "").strip()
    if user:
        return user.split("@")[0]
    return "this address"


def _count_phrase(count: int, *, kind: str) -> str:
    """Speakable count for mail pieces or expected packages."""
    if count <= 0:
        return "no mail" if kind == "mail" else "no packages"
    if count == 1:
        return "one piece of mail" if kind == "mail" else "one package"
    if kind == "mail":
        return f"{count} pieces of mail"
    return f"{count} packages"


def build_mail_tts_message(accounts: list[dict[str, Any]]) -> str:
    """
    Build a per-address daily mail summary.

    Each enabled account is announced with separate mail and package counts.
    Explicitly states when an address has none expected today.
    """
    enabled = [a for a in accounts if a.get("enabled", True)]
    if not enabled:
        return "no mail accounts are configured."

    lines: list[str] = []
    for account in enabled:
        name = _account_label(account)
        mailpieces = int(account.get("mailpiece_count") or 0)
        packages = int(account.get("package_count") or 0)

        if mailpieces == 0 and packages == 0:
            lines.append(f"for {name}, there is no mail and no packages expected today")
            continue

        mail_part = _count_phrase(mailpieces, kind="mail")
        package_part = _count_phrase(packages, kind="package")
        lines.append(f"for {name}, {mail_part} and {package_part} expected today")

    if not lines:
        return "there is no mail and no packages expected today."

    if len(lines) == 1:
        return f"{lines[0]}."
    return f"{'. '.join(lines)}."


async def trigger_mail_tts(accounts: list[dict[str, Any]]) -> None:
    """Trigger TTS announcement with mail and package counts per address."""
    if not await _should_announce("mail_arrived"):
        logger.debug("TTS skipped for mail_arrived")
        return

    message = build_mail_tts_message(accounts)
    logger.info(f"Sending mail TTS: {message}")
    config = await config_store.load()
    await dispatch_tts(config, message, "mail_arrived")


async def trigger_package_tts(
    package: dict[str, Any],
    status_changed: bool = False,
    newly_ofd: bool = False,
    newly_delivered: bool = False,
) -> None:
    """Trigger TTS announcement for package status change."""
    if newly_delivered:
        event_type = "delivered"
    elif newly_ofd:
        event_type = "out_for_delivery"
    else:
        event_type = "status_change"

    if not await _should_announce(event_type):
        logger.debug(f"TTS skipped for {event_type}")
        return

    message = _build_package_message(
        package,
        status_changed=status_changed,
        newly_ofd=newly_ofd,
        newly_delivered=newly_delivered,
    )

    logger.info(f"Sending package TTS ({event_type}): {message}")
    config = await config_store.load()
    await dispatch_tts(config, message, event_type)

