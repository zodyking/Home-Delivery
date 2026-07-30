"""
TTS trigger logic for package status changes and mail delivery.
Respects quiet hours and user preferences.
"""
from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any

from config_store import config_store
from tts_engine import send_tts

logger = logging.getLogger(__name__)


def _parse_time(t: str) -> time | None:
    """Parse HH:MM time string."""
    try:
        parts = t.split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return None


def _in_quiet_hours(start: str, end: str) -> bool:
    """Check if current time is within allowed announcement hours."""
    now = datetime.now().time()
    start_time = _parse_time(start)
    end_time = _parse_time(end)

    if not start_time or not end_time:
        return False

    # Handle day-spanning ranges (e.g., 22:00 to 08:00)
    if start_time <= end_time:
        return not (start_time <= now <= end_time)
    else:
        return end_time < now < start_time


async def _should_announce(event_type: str) -> bool:
    """Check if TTS should be triggered based on settings."""
    config = await config_store.load()
    tts = config.get("tts", {})

    if not tts.get("enabled"):
        return False

    # Check event type toggle
    toggle_map = {
        "status_change": "enable_status_change",
        "out_for_delivery": "enable_out_for_delivery",
        "delivered": "enable_delivered",
        "mail_arrived": "enable_mail_arrived",
    }

    toggle_key = toggle_map.get(event_type)
    if toggle_key and not tts.get(toggle_key, True):
        return False

    # Check quiet hours
    start = tts.get("start_time", "08:00")
    end = tts.get("end_time", "21:00")

    if _in_quiet_hours(start, end):
        logger.debug(f"TTS skipped: outside announcement hours ({start} - {end})")
        return False

    return True


async def _get_media_players() -> list[dict[str, Any]]:
    """Get configured media players."""
    config = await config_store.load()
    return config.get("media_players", [])


def _build_package_message(
    package: dict[str, Any],
    status_changed: bool = False,
    newly_ofd: bool = False,
    newly_delivered: bool = False,
) -> str:
    """Build TTS message for package status."""
    recipient = package.get("recipient")
    destination = package.get("destination")
    status = package.get("status", "")
    carrier = (package.get("carrier") or "").upper()

    # Build recipient/destination context
    context = ""
    if recipient and destination:
        context = f" for {recipient} going to {destination}"
    elif recipient:
        context = f" for {recipient}"
    elif destination:
        context = f" going to {destination}"

    if newly_delivered:
        return f"Great news! Your {carrier} package{context} has been delivered."

    if newly_ofd:
        return f"Heads up! Your {carrier} package{context} is out for delivery."

    if status_changed:
        # Get latest event for details
        events = package.get("events", [])
        location = ""
        if events:
            location = events[0].get("location", "")

        msg = f"Package update: Your {carrier} package{context} status is now {status}."
        if location:
            msg += f" Last seen in {location}."
        return msg

    return f"Package status: {status}"


async def trigger_package_tts(
    package: dict[str, Any],
    status_changed: bool = False,
    newly_ofd: bool = False,
    newly_delivered: bool = False,
) -> None:
    """
    Trigger TTS announcement for package status change.

    Args:
        package: The package data dict.
        status_changed: True if status changed from previous poll.
        newly_ofd: True if package just went out for delivery.
        newly_delivered: True if package was just delivered.
    """
    # Determine event type for toggle check
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

    logger.info(f"Sending package TTS: {message}")

    media_players = await _get_media_players()
    if not media_players:
        logger.warning("No media players configured for TTS")
        return

    for mp in media_players:
        entity_id = mp.get("entity_id")
        if not entity_id:
            continue

        try:
            await send_tts(
                message=message,
                media_player=entity_id,
                volume=mp.get("volume", 0.7),
                tts_entity=mp.get("tts_entity_id", "tts.google_translate_say"),
                preroll_ms=mp.get("preroll_ms", 0),
            )
        except Exception as e:
            logger.error(f"TTS failed for {entity_id}: {e}")


async def trigger_mail_tts(piece_count: int) -> None:
    """
    Trigger TTS announcement for mail arrival.

    Args:
        piece_count: Number of mail pieces.
    """
    if not await _should_announce("mail_arrived"):
        logger.debug("TTS skipped for mail_arrived")
        return

    if piece_count == 1:
        message = "You have 1 piece of mail arriving today."
    else:
        message = f"You have {piece_count} pieces of mail arriving today."

    logger.info(f"Sending mail TTS: {message}")

    media_players = await _get_media_players()
    if not media_players:
        logger.warning("No media players configured for TTS")
        return

    for mp in media_players:
        entity_id = mp.get("entity_id")
        if not entity_id:
            continue

        try:
            await send_tts(
                message=message,
                media_player=entity_id,
                volume=mp.get("volume", 0.7),
                tts_entity=mp.get("tts_entity_id", "tts.google_translate_say"),
                preroll_ms=mp.get("preroll_ms", 0),
            )
        except Exception as e:
            logger.error(f"TTS failed for {entity_id}: {e}")
