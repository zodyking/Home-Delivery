"""
Street address autocomplete via Photon (OpenStreetMap, free, no API key).

Returns street-level lines only — house number, street name, and unit when
available. City, state, and ZIP are omitted from stored values.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

PHOTON_URL = "https://photon.komoot.io/api/"
_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}
_CACHE_TTL_SECONDS = 300
_MIN_REQUEST_INTERVAL = 1.0
_last_request_at = 0.0
_request_lock = asyncio.Lock()


def _street_from_properties(props: dict[str, Any]) -> str | None:
    """Build a speakable street line from Photon/Nominatim address parts."""
    housenumber = str(props.get("housenumber") or props.get("house_number") or "").strip()
    street = str(props.get("street") or props.get("road") or "").strip()
    unit = str(props.get("unit") or props.get("addr_unit") or "").strip()

    place_type = str(props.get("type") or props.get("osm_value") or "").lower()
    if place_type in {"city", "state", "country", "postcode", "county", "region"}:
        return None

    if not street and not housenumber:
        name = str(props.get("name") or "").strip()
        if place_type in {"street", "road", "residential", "pedestrian", "tertiary", "secondary", "primary"}:
            street = name
        elif name and housenumber:
            street = name
        else:
            return None

    parts: list[str] = []
    if housenumber:
        parts.append(housenumber)
    if street:
        parts.append(street)

    line = " ".join(parts).strip()
    if not line:
        return None

    if unit:
        unit_upper = unit.upper()
        if not unit_upper.startswith(("APT", "UNIT", "STE", "SUITE", "#")):
            unit = f"APT {unit}"
        line = f"{line} {unit}"

    return line


def _dedupe_suggestions(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for item in items:
        key = item["street_address"].casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


async def _rate_limit() -> None:
    global _last_request_at
    async with _request_lock:
        now = time.monotonic()
        elapsed = now - _last_request_at
        if elapsed < _MIN_REQUEST_INTERVAL:
            await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        _last_request_at = time.monotonic()


async def _photon_search(query: str, limit: int) -> list[dict[str, str]]:
    params = {
        "q": query,
        "limit": str(max(limit, 8)),
        "lang": "en",
    }
    headers = {"Accept": "application/json", "User-Agent": "HomeDelivery/1.0 (Home Assistant addon)"}

    await _rate_limit()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(PHOTON_URL, params=params, headers=headers, timeout=8) as resp:
                if resp.status != 200:
                    logger.warning("Photon autocomplete HTTP %s", resp.status)
                    return []
                data = await resp.json()
    except Exception as exc:
        logger.warning("Photon autocomplete failed: %s", exc)
        return []

    suggestions: list[dict[str, str]] = []
    for feature in data.get("features") or []:
        props = feature.get("properties") or {}
        country = str(props.get("countrycode") or "").upper()
        if country and country != "US":
            continue

        street_address = _street_from_properties(props)
        if not street_address:
            continue

        suggestions.append({"street_address": street_address})

    return _dedupe_suggestions(suggestions)[:limit]


async def search_street_addresses(query: str, limit: int = 8) -> list[dict[str, str]]:
    """Search for street address suggestions matching the query."""
    cleaned = (query or "").strip()
    if len(cleaned) < 3:
        return []

    cache_key = cleaned.casefold()
    cached = _CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1][:limit]

    results = await _photon_search(cleaned, limit)
    _CACHE[cache_key] = (time.time(), results)
    return results
