"""
TTS engine for Home Delivery.
Sends announcements via Home Assistant Supervisor API.

Media-player resolution (skip / per-type volume) mirrors home-weather's
resolve_announcement_players + send_tts pattern.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

HA_BASE = "http://supervisor/core"
IDLE_STATES = ("idle", "unknown", "unavailable", "off", "standby")
MAX_WAIT_SECONDS = 300
POLL_INTERVAL = 2

# Delivery announcement type IDs (keys under announcement_players)
ANNOUNCEMENT_TYPES = (
    "status_change",
    "out_for_delivery",
    "delivered",
    "mail_arrived",
)


async def _ha_request(
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """Make a request to the Home Assistant REST API."""
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


async def _get_volume_level(entity_id: str) -> float | None:
    """Read current volume_level attribute if available."""
    status, data = await _ha_request("GET", f"/api/states/{entity_id}")
    if status != 200 or not data:
        return None
    attrs = data.get("attributes") or {}
    vol = attrs.get("volume_level")
    try:
        return float(vol) if vol is not None else None
    except (TypeError, ValueError):
        return None


async def _wait_for_idle(entity_id: str) -> bool:
    """Wait until media player is idle."""
    elapsed = 0
    while elapsed < MAX_WAIT_SECONDS:
        state = await _get_media_player_state(entity_id)
        if state in IDLE_STATES:
            return True
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
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


def resolve_announcement_players(
    config: dict[str, Any],
    type_id: str,
    media_players: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Resolve media players for an announcement type (home-weather parity).

    - Drops players with bypass=True for this type
    - Applies per-type volume override when set
    - Requires entity_id + tts_entity_id
    """
    players = media_players if media_players is not None else (config.get("media_players") or [])
    announcement_players = config.get("announcement_players") or {}
    type_overrides = announcement_players.get(type_id) or {}

    resolved: list[dict[str, Any]] = []
    for mp in players:
        entity_id = mp.get("entity_id")
        if not entity_id or not mp.get("tts_entity_id"):
            continue

        override = type_overrides.get(entity_id) or {}
        if override.get("bypass", False):
            continue

        player_copy = dict(mp)
        if "volume" in override:
            try:
                player_copy["volume"] = max(0.0, min(1.0, float(override["volume"])))
            except (TypeError, ValueError):
                pass
        resolved.append(player_copy)

    return resolved


def apply_message_prefix(config: dict[str, Any], message: str) -> str:
    """Prepend configured intro message when present (comma join for natural speech)."""
    prefix = (config.get("message_prefix") or "").strip().rstrip(",.")
    body = (message or "").strip()
    if not body:
        return ""
    if not prefix:
        return body
    if body.lower().startswith(prefix.lower()):
        return body
    return f"{prefix}, {body}"


async def send_tts(
    message: str,
    media_player: str,
    volume: float = 0.7,
    wait_for_idle: bool = True,
    tts_entity: str = "tts.google_translate_say",
    language: str = "",
    preroll_ms: int = 0,
    cache: bool = True,
) -> bool:
    """Send a TTS announcement to one media player via Home Assistant."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        logger.warning("Cannot send TTS: SUPERVISOR_TOKEN not available")
        return False

    logger.info(f"Sending TTS to {media_player}: {message[:50]}...")

    previous_volume = await _get_volume_level(media_player)

    if wait_for_idle:
        await _wait_for_idle(media_player)

    await _set_volume(media_player, volume)

    if preroll_ms > 0:
        await asyncio.sleep(preroll_ms / 1000)

    service_data: dict[str, Any] = {
        "entity_id": tts_entity,
        "media_player_entity_id": media_player,
        "message": message,
        "cache": bool(cache),
    }
    if language:
        service_data["language"] = language

    status, _response = await _ha_request("POST", "/api/services/tts/speak", service_data)
    ok = status == 200

    if not ok:
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
        ok = status == 200

    if ok:
        logger.info("TTS sent successfully to %s", media_player)
    else:
        logger.error("TTS failed for %s with status %s", media_player, status)

    # Restore prior volume after a short beat (best-effort)
    if previous_volume is not None:
        await asyncio.sleep(0.5)
        await _set_volume(media_player, previous_volume)

    return ok


async def dispatch_tts(
    config: dict[str, Any],
    message: str,
    type_id: str,
) -> int:
    """
    Speak on all non-skipped players for an announcement type.

    Returns number of successful sends.
    """
    text = apply_message_prefix(config, message)
    if not text:
        return 0

    players = resolve_announcement_players(config, type_id)
    if not players:
        logger.warning("No eligible media players for announcement type %s", type_id)
        return 0

    sent = 0
    for mp in players:
        entity_id = mp.get("entity_id")
        try:
            ok = await send_tts(
                message=text,
                media_player=entity_id,
                volume=float(mp.get("volume", 0.6)),
                tts_entity=mp.get("tts_entity_id") or "tts.google_translate_say",
                language=mp.get("language") or "",
                preroll_ms=int(mp.get("preroll_ms") or 0),
                cache=bool(mp.get("cache", True)),
            )
            if ok:
                sent += 1
        except Exception as exc:
            logger.error("TTS failed for %s: %s", entity_id, exc)
    return sent


async def send_notification(
    title: str,
    message: str,
    notify_service: str = "notify.notify",
) -> bool:
    """Send a notification via Home Assistant."""
    service_name = notify_service.replace("notify.", "")
    status, _ = await _ha_request(
        "POST",
        f"/api/services/notify/{service_name}",
        {"title": title, "message": message},
    )
    return status == 200
