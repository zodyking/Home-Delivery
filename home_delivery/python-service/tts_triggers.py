"""
TTS trigger logic for package status changes and mail delivery.
Respects quiet hours, per-type toggles, media-player skip/volume, and message prefix.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, time
from typing import Any

from config_store import config_store
from tts_address import format_address_for_tts, format_location_for_tts
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
    """Normalize a single status fragment for TTS."""
    text = re.sub(r"\s+", " ", (status or "").strip().lower())
    text = text.replace("_", " ")
    text = re.sub(r"\bawaiting\b", "waiting", text)
    return text


_UNHELPFUL_STATUS = frozenset({"", "pending", "unknown", "updated", "status update"})


def _status_fragment(text: str | None) -> str:
    normalized = _format_status(text)
    return "" if normalized in _UNHELPFUL_STATUS else normalized


def _package_status_for_tts(package: dict[str, Any]) -> str:
    """
    Build a speakable combined status from detail + headline status + latest event.

    UI often stores the milestone in status_detail (e.g. "Shipping Label Created")
    and a carrier sub-status in status (e.g. "USPS Awaiting Item").
    """
    detail = _status_fragment(package.get("status_detail"))
    headline = _status_fragment(package.get("status"))

    events = package.get("events") or []
    event_text = ""
    if events and isinstance(events[0], dict):
        ev = events[0]
        event_text = _status_fragment(
            ev.get("description") or ev.get("status") or ev.get("detail")
        )

    parts: list[str] = []
    for candidate in (detail, headline, event_text):
        if not candidate:
            continue
        if any(candidate in existing or existing in candidate for existing in parts):
            continue
        parts.append(candidate)

    if not parts:
        return "updated"

    return " ".join(parts)


def _speak_address(text: str | None) -> str:
    """Format an address or destination label for clear TTS."""
    return format_address_for_tts(text or "")


def _speak_location(text: str | None) -> str:
    """Format a tracking scan location as city and state for TTS."""
    return format_location_for_tts(text or "")


def _package_phrase(
    carrier: str | None,
    recipient: str | None,
    destination: str | None,
) -> str:
    """Natural mid-sentence phrase, e.g. 'the UPS package for Mom'."""
    name = _format_carrier(carrier)
    recipient = (recipient or "").strip()
    if recipient.lower() == "someone":
        recipient = ""
    destination = _speak_address(destination)

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

    status_text = _package_status_for_tts(package)

    if status_changed:
        events = package.get("events", [])
        location = ""
        if events and isinstance(events[0], dict):
            location = (events[0].get("location") or "").strip()

        msg = f"{phrase}. Package status is now {status_text}."
        spoken_loc = _speak_location(location)
        if spoken_loc:
            msg += f" Last seen in {spoken_loc}."
        return msg

    return f"{phrase}. Package status is now {status_text}."


def _account_label(account: dict[str, Any]) -> str:
    """Human-readable address name for TTS."""
    label = (account.get("label") or "").strip()
    if label:
        return _speak_address(label)
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


async def trigger_daily_digest_tts(accounts: list[dict[str, Any]] | None = None) -> None:
    """Trigger repeating Daily Digest TTS with current mail/package counts."""
    if not await _should_announce("mail_arrived"):
        logger.debug("TTS skipped for daily digest")
        return

    if accounts is None:
        config = await config_store.load()
        accounts = [
            a for a in config.get("mail", {}).get("accounts", [])
            if a.get("enabled", True)
        ]

    message = build_mail_tts_message(accounts)
    logger.info("Sending daily digest TTS: %s", message)
    config = await config_store.load()
    await dispatch_tts(config, message, "mail_arrived")

    tts = dict(config.get("tts") or {})
    tts["last_daily_digest_at"] = datetime.now().isoformat()
    await config_store.update({"tts": tts})


async def trigger_mail_tts(accounts: list[dict[str, Any]]) -> None:
    """Deprecated alias — use trigger_daily_digest_tts for scheduled digests."""
    await trigger_daily_digest_tts(accounts)


def _pick_package_for_test(
    packages: list[dict[str, Any]],
    event_type: str,
) -> dict[str, Any] | None:
    if not packages:
        return None

    if event_type == "delivered":
        delivered = [p for p in packages if p.get("delivered")]
        if delivered:
            return delivered[0]

    if event_type == "out_for_delivery":
        ofd = [p for p in packages if p.get("out_for_delivery") and not p.get("delivered")]
        if ofd:
            return ofd[0]

    active = [p for p in packages if not p.get("delivered")]
    if active:
        return active[0]
    return packages[0]


async def build_announcement_test_message(type_id: str) -> str:
    """Build a test announcement from live dashboard data when available."""
    config = await config_store.load()
    packages = await config_store.get_packages()
    mail_state = await config_store.get_mail_state()
    accounts = [
        a for a in (mail_state.get("accounts") or config.get("mail", {}).get("accounts", []))
        if a.get("enabled", True)
    ]

    if type_id == "mail_arrived":
        if accounts:
            return build_mail_tts_message(accounts)
        return "no mail accounts are configured."

    package = _pick_package_for_test(packages, type_id)
    if package:
        if type_id == "delivered":
            return _build_package_message(package, newly_delivered=True)
        if type_id == "out_for_delivery":
            return _build_package_message(package, newly_ofd=True)
        return _build_package_message(package, status_changed=True)

    fallbacks = {
        "status_change": "no active packages to announce.",
        "out_for_delivery": "no packages are out for delivery right now.",
        "delivered": "no delivered packages to announce.",
    }
    return fallbacks.get(type_id, "no announcement data is available yet.")


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

