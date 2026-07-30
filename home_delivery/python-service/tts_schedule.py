"""
Scheduled Daily Digest announcements (home-weather-style repeat windows).
"""
from __future__ import annotations

from datetime import datetime, time

_DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse_time_hm(value: str | None, default_h: int, default_m: int) -> tuple[int, int]:
    """Parse HH:MM into hour/minute."""
    if not value or not isinstance(value, str):
        return default_h, default_m
    try:
        hour_str, minute_str = value.split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
    except (TypeError, ValueError):
        return default_h, default_m
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    return hour, minute


def _safe_int(value, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    if maximum is not None and parsed > maximum:
        return maximum
    return parsed


def _minutes_in_window(now_minutes: int, start_minutes: int, end_minutes: int) -> bool:
    if start_minutes <= end_minutes:
        return start_minutes <= now_minutes <= end_minutes
    return now_minutes >= start_minutes or now_minutes <= end_minutes


def _allowed_weekday(now: datetime, days_of_week: list[str] | None) -> bool:
    if not days_of_week:
        return True
    allowed = {d.lower()[:3] for d in days_of_week if isinstance(d, str)}
    if not allowed:
        return True
    return _DAY_KEYS[now.weekday()] in allowed


def compute_daily_digest_anchor_minute(start_minutes: int, minute_offset: int) -> int:
    """First digest slot minute-of-day at or after start_minutes with given offset."""
    offset = max(0, min(59, minute_offset))
    anchor = (start_minutes // 60) * 60 + offset
    if anchor < start_minutes:
        anchor += 60
    return anchor


def should_fire_daily_digest(
    now: datetime,
    tts_config: dict,
    *,
    last_fired: datetime | None = None,
) -> bool:
    """
    Return True when ``now`` matches a Daily Digest repeat slot.

    Uses global active hours (start_time/end_time), repeat interval (hours + minutes),
    and minute offset — same model as home-weather scheduled forecasts, with optional
    sub-hour repeat intervals (e.g. every 1h 30m at :45).
    """
    if not tts_config.get("enabled"):
        return False
    if not tts_config.get("enable_mail_arrived", True):
        return False

    repeat_hours = _safe_int(tts_config.get("daily_digest_repeat_hours"), 1, minimum=0, maximum=12)
    repeat_minutes = _safe_int(tts_config.get("daily_digest_repeat_minutes"), 30, minimum=0, maximum=59)
    interval_minutes = repeat_hours * 60 + repeat_minutes
    if interval_minutes <= 0:
        return False

    minute_offset = _safe_int(tts_config.get("daily_digest_minute_offset"), 0, minimum=0, maximum=59)
    start_h, start_m = parse_time_hm(tts_config.get("start_time"), 8, 0)
    end_h, end_m = parse_time_hm(tts_config.get("end_time"), 21, 0)

    if not _allowed_weekday(now, tts_config.get("days_of_week")):
        return False

    now_minutes = now.hour * 60 + now.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    if not _minutes_in_window(now_minutes, start_minutes, end_minutes):
        return False

    anchor = compute_daily_digest_anchor_minute(start_minutes, minute_offset)
    if now_minutes < anchor:
        return False

    elapsed = now_minutes - anchor
    if elapsed % interval_minutes != 0:
        return False

    if last_fired and last_fired.date() == now.date():
        delta = (now - last_fired).total_seconds()
        if delta < max(55, interval_minutes * 60 * 0.5):
            return False

    return True
