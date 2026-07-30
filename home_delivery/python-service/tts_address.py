"""
Format street addresses and tracking locations for TTS.

Address example: "443 Linwood APT 2F" -> "4 4 3 linwood, A P T 2 F"
Location example: "INDEPENDENCE, OH 44131" -> "Independence, Ohio"
"""
from __future__ import annotations

import re

_UNIT_MARKERS = frozenset({
    "APT", "APARTMENT", "UNIT", "STE", "SUITE", "FL", "FLOOR",
    "RM", "ROOM", "#", "BLDG", "BUILDING", "LOT", "SLIP", "DEPT",
    "DEPARTMENT", "TRLR", "TRAILER", "BSMT", "BASEMENT", "PH", "PENTHOUSE",
})

_STREET_SUFFIXES = frozenset({
    "ST", "STREET", "AVE", "AVENUE", "RD", "ROAD", "DR", "DRIVE", "LN", "LANE",
    "BLVD", "BOULEVARD", "CT", "COURT", "PL", "PLACE", "WAY", "PKWY", "PARKWAY",
    "CIR", "CIRCLE", "TER", "TERRACE", "HWY", "HIGHWAY",
})

_US_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}

_COUNTRY_TOKENS = frozenset({"US", "USA", "UNITED", "STATES"})


def _spell_chars(token: str) -> str:
    parts: list[str] = []
    for ch in token:
        if ch.isdigit():
            parts.append(ch)
        elif ch.isalpha():
            parts.append(ch.upper())
    return " ".join(parts)


def _format_token(token: str) -> str:
    cleaned = token.strip().lstrip("#")
    if not cleaned:
        return ""

    if cleaned.isdigit():
        return " ".join(cleaned)

    upper = cleaned.upper()
    if upper in _UNIT_MARKERS:
        return _spell_chars(upper)

    has_alpha = any(ch.isalpha() for ch in cleaned)
    has_digit = any(ch.isdigit() for ch in cleaned)
    if has_alpha and has_digit:
        return _spell_chars(cleaned)

    if cleaned.isalpha() and cleaned.isupper() and len(cleaned) <= 5:
        if upper in _STREET_SUFFIXES:
            return cleaned.lower()
        return _spell_chars(cleaned)

    if cleaned.isalpha():
        return cleaned.lower()

    return _spell_chars(cleaned)


def format_address_for_tts(address: str) -> str:
    """
    Speak an address with digit-by-digit numbers and spelled unit codes.

    Hyphens are ignored (treated as separators). Commas are inserted before
    unit designators (APT, UNIT, #, etc.) when present.
    """
    text = (address or "").strip()
    if not text:
        return ""

    normalized = re.sub(r"[-–—]", " ", text)
    normalized = re.sub(r"[,;]+", " ", normalized)
    tokens = [t for t in re.split(r"\s+", normalized) if t]
    if not tokens:
        return ""

    unit_index = next(
        (i for i, tok in enumerate(tokens) if tok.strip().lstrip("#").upper() in _UNIT_MARKERS),
        None,
    )

    if unit_index is not None and unit_index > 0:
        street_parts = [_format_token(t) for t in tokens[:unit_index]]
        unit_parts = [_format_token(t) for t in tokens[unit_index:]]
        street = " ".join(p for p in street_parts if p)
        unit = " ".join(p for p in unit_parts if p)
        if street and unit:
            return f"{street}, {unit}"
        return street or unit

    return " ".join(p for t in tokens if (p := _format_token(t)))


def format_location_for_tts(location: str) -> str:
    """
    Speak a tracking location as city and state only — no ZIP or street numbers.

    Examples:
        "INDEPENDENCE, OH 44131" -> "Independence, Ohio"
        "BROOKLYN, NY US" -> "Brooklyn, New York"
    """
    text = (location or "").strip()
    if not text:
        return ""

    # Drop ZIP codes and plus-four extensions.
    text = re.sub(r"\b\d{5}(?:-\d{4})?\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")

    if not text:
        return ""

    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return text.lower()

    city = parts[0].title()
    state_part = parts[1] if len(parts) > 1 else ""

    state_tokens = [
        tok for tok in state_part.split()
        if tok.upper() not in _COUNTRY_TOKENS
    ]
    if not state_tokens and len(parts) > 2:
        state_tokens = [
            tok for tok in parts[2].split()
            if tok.upper() not in _COUNTRY_TOKENS
        ]

    if not state_tokens:
        return city

    state_abbr = state_tokens[0].upper()
    if len(state_abbr) == 2 and state_abbr.isalpha():
        state_name = _US_STATE_NAMES.get(state_abbr, state_abbr)
        return f"{city}, {state_name}"

    return f"{city}, {' '.join(state_tokens).title()}"
