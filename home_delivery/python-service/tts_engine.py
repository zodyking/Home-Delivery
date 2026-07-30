"""
TTS engine for Home Delivery.
Sends announcements via Home Assistant Supervisor API.
Ported from HA-ConEd ha_tts.py pattern.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Home Assistant Supervisor API base
HA_BASE = "http://supervisor/core"

# Media player states considered "idle"
IDLE_STATES = ("idle", "unknown", "unavailable", "off", "standby")

# Max time to wait for media player to become idle
MAX_WAIT_SECONDS = 300
POLL_INTERVAL = 2


async def _ha_request(
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """
    Make a request to the Home Assistant REST API.

    Returns:
        Tuple of (status_code, response_json or None).
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        logger.warning("SUPERVISOR_TOKEN not set — not running as HA addon")
        return 401, None

    url = f"{HA_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            kwargs: dict[str, Any] = {"headers": headers}
            if json_body is not None:
                kwargs["json"] = json_body

            async with session.request(method, url, **kwargs) as resp:
                data = None
                if resp.content_type and "json" in resp.content_type:
                    try:
                        data = await resp.json()
                    except Exception:
                        pass
                return resp.status, data

    except Exception as e:
        logger.error(f"HA request failed: {e}")
        return 500, None


async def _get_media_player_state(entity_id: str) -> str | None:
    """Get current state of a media player."""
    status, data = await _ha_request("GET", f"/api/states/{entity_id}")
    if status != 200 or not data:
        return None
    return data.get("state")


async def _wait_for_idle(entity_id: str) -> bool:
    """Wait until media player is idle."""
    elapsed = 0
    while elapsed < MAX_WAIT_SECONDS:
        state = await _get_media_player_state(entity_id)
        if state in IDLE_STATES:
            return True
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        logger.debug(f"Media player {entity_id} state={state}, waiting...")

    logger.warning(f"Timeout waiting for {entity_id} to become idle")
    return False


async def _set_volume(entity_id: str, volume: float) -> bool:
    """Set media player volume."""
    status, _ = await _ha_request(
        "POST",
        "/api/services/media_player/volume_set",
        {
            "entity_id": entity_id,
            "volume_level": max(0.0, min(1.0, volume)),
        },
    )
    return status == 200


async def send_tts(
    message: str,
    media_player: str,
    volume: float = 0.7,
    wait_for_idle: bool = True,
    tts_entity: str = "tts.google_translate_say",
    language: str = "",
    preroll_ms: int = 0,
) -> bool:
    """
    Send a TTS announcement via Home Assistant.

    Args:
        message: The text to speak.
        media_player: The media_player entity ID.
        volume: Volume level (0.0 - 1.0).
        wait_for_idle: Whether to wait for the player to be idle first.
        tts_entity: The TTS service entity (e.g., tts.google_translate_say).
        language: Optional language code.
        preroll_ms: Delay before speaking (for speaker wake-up).

    Returns:
        True if successful.
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        logger.warning("Cannot send TTS: SUPERVISOR_TOKEN not available")
        return False

    logger.info(f"Sending TTS to {media_player}: {message[:50]}...")

    # Wait for player to be idle
    if wait_for_idle:
        await _wait_for_idle(media_player)

    # Set volume
    await _set_volume(media_player, volume)

    # Preroll delay
    if preroll_ms > 0:
        await asyncio.sleep(preroll_ms / 1000)

    # Build TTS service call
    # Try new tts.speak first (HA 2023.x+), fall back to legacy
    service_data: dict[str, Any] = {
        "entity_id": tts_entity,
        "media_player_entity_id": media_player,
        "message": message,
    }

    if language:
        service_data["language"] = language

    status, response = await _ha_request(
        "POST",
        "/api/services/tts/speak",
        service_data,
    )

    if status == 200:
        logger.info("TTS sent successfully (tts.speak)")
        return True

    # Fallback to legacy tts.{service}_say pattern
    tts_service = tts_entity.replace("tts.", "")
    legacy_data: dict[str, Any] = {
        "entity_id": media_player,
        "message": message,
    }
    if language:
        legacy_data["language"] = language

    status, _ = await _ha_request(
        "POST",
        f"/api/services/tts/{tts_service}",
        legacy_data,
    )

    if status == 200:
        logger.info(f"TTS sent successfully (legacy tts.{tts_service})")
        return True

    logger.error(f"TTS failed with status {status}")
    return False


async def send_notification(
    title: str,
    message: str,
    notify_service: str = "notify.notify",
) -> bool:
    """
    Send a notification via Home Assistant.

    Args:
        title: Notification title.
        message: Notification body.
        notify_service: The notify service to use.

    Returns:
        True if successful.
    """
    service_name = notify_service.replace("notify.", "")

    status, _ = await _ha_request(
        "POST",
        f"/api/services/notify/{service_name}",
        {
            "title": title,
            "message": message,
        },
    )

    return status == 200
