"""
OCR helpers for Informed Delivery letter scans.

Extracts sender (from) and recipient (to) from window-envelope images using
region-aware Tesseract passes and plausibility checks.
"""
from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL import Image

from mail.image_enhance import enhance_mail_preview

logger = logging.getLogger(__name__)

# US city / state / ZIP line, e.g. BROOKLYN NY 11208-2187
CITY_STATE_ZIP_RE = re.compile(
    r"^([A-Z][A-Z .'-]+?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$"
)
STREET_SUFFIX_RE = (
    r"(?:ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|DR|DRIVE|LN|LANE|CT|COURT|"
    r"WAY|PL|PLACE|PKWY|PARKWAY|HWY|HIGHWAY|CIR|CIRCLE)"
)
ADDRESS_LINE_RE = re.compile(
    rf"^(?:P\.?O\.?\s*BOX\s+\d+|\d+\s+[A-Z0-9 .#/-]*(?:{STREET_SUFFIX_RE})(?:\s+(?:APT|APARTMENT|UNIT|STE|SUITE|#)\s*[A-Z0-9-]+)?.*)$",
    re.IGNORECASE,
)
NOISE_RE = re.compile(
    r"^(?:"
    r"UAC(?:\s*[-:]?\s*\d+)?"
    r"|FIRST[- ]CLASS(?:\s+MAIL)?"
    r"|PRESORTED(?:\s+STD)?"
    r"|U\.?S\.?\s*POSTAGE(?:\s+PAID)?"
    r"|POSTAGE(?:\s+PAID)?"
    r"|MAIL"
    r"|RIS"
    r"|PAID"
    r"|PERMIT"
    r"|\d{5}\$\d+(?:\s+C\d+)?"
    r"|C\d{3}"
    r")$",
    re.IGNORECASE,
)
POSTAGE_KEYWORDS = (
    "postage",
    "presorted",
    "first-class",
    "first class",
    "u.s. postage",
    "us postage",
    "automation",
    "permit",
    "comingle",
    "netstamps",
    "prsrtt",
    "electronic service",
)
REF_JUNK_RE = re.compile(
    r"^\d{5,}/\d{5,}|"
    r"^\*?\s*[\d\s-]{8,}\*?$|"
    r"^\d{5,}/\d{5,}/\d{5,}.*$|"
    r"^[A-Z]{0,3}\d{4,}[A-Z0-9]*$|"
    r"^USBP\d+$"
)

# Normalized envelope regions (left side only — postage indicia is top-right).
RETURN_REGION = (0.0, 0.0, 0.58, 0.44)
RECIPIENT_REGION = (0.0, 0.36, 0.72, 1.0)
POSTAGE_X_CUTOFF = 0.52


def _ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


def _preprocess_for_ocr(img: "Image.Image") -> "Image.Image":
    from PIL import Image, ImageFilter, ImageOps

    enhanced = enhance_mail_preview(img)
    gray = ImageOps.grayscale(enhanced)
    if gray.width < 1200:
        scale = max(2, int(1200 / max(gray.width, 1)))
        gray = gray.resize((gray.width * scale, gray.height * scale), Image.Resampling.LANCZOS)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = gray.filter(ImageFilter.SHARPEN)
    try:
        import numpy as np

        arr = np.array(gray, dtype=np.uint8)
        threshold = max(120, min(210, int(np.percentile(arr, 58))))
        binary = (arr > threshold).astype(np.uint8) * 255
        from PIL import Image as PILImage

        return PILImage.fromarray(binary, mode="L")
    except Exception:
        return gray


def _crop_fraction(img: Image.Image, box: tuple[float, float, float, float]) -> Image.Image:
    w, h = img.size
    left = int(box[0] * w)
    top = int(box[1] * h)
    right = max(left + 1, int(box[2] * w))
    bottom = max(top + 1, int(box[3] * h))
    return img.crop((left, top, right, bottom))


def _ocr_image(img: Image.Image, *, psm: int = 6) -> str:
    if not _ocr_available():
        return ""
    try:
        import pytesseract

        processed = _preprocess_for_ocr(img)
        config = f"--psm {psm} -c preserve_interword_spaces=1"
        return pytesseract.image_to_string(processed, config=config) or ""
    except Exception as exc:
        logger.warning("Letter OCR failed: %s", exc)
        return ""


def _ocr_spatial(img: Image.Image) -> str:
    """OCR while ignoring words in the postage band on the right."""
    if not _ocr_available():
        return ""
    try:
        import pytesseract

        processed = _preprocess_for_ocr(img)
        data = pytesseract.image_to_data(
            processed,
            config="--psm 6 -c preserve_interword_spaces=1",
            output_type=pytesseract.Output.DICT,
        )
        width = processed.width
        cutoff = int(width * POSTAGE_X_CUTOFF)
        rows: dict[tuple[int, int, int], list[str]] = {}
        for i, word in enumerate(data.get("text") or []):
            token = (word or "").strip()
            if not token:
                continue
            conf = int(data["conf"][i]) if data["conf"][i] not in ("-1", -1) else 0
            if conf < 35:
                continue
            left = int(data["left"][i])
            if left >= cutoff:
                continue
            key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
            rows.setdefault(key, []).append(token)
        lines = [" ".join(words) for words in rows.values()]
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("Spatial OCR fallback to block OCR: %s", exc)
        return _ocr_image(img, psm=6)


def _collect_ocr_candidates(img: Image.Image) -> list[str]:
    base = img.convert("RGB")
    candidates = [
        _ocr_spatial(base),
        _ocr_image(_crop_fraction(base, RETURN_REGION), psm=6),
        _ocr_image(_crop_fraction(base, RECIPIENT_REGION), psm=6),
        _ocr_image(base, psm=4),
        _ocr_image(base, psm=11),
    ]
    return [text for text in candidates if text.strip()]


def _is_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if NOISE_RE.match(stripped):
        return True
    if REF_JUNK_RE.match(stripped):
        return True
    lower = stripped.lower().strip(" ,.;:")
    if any(keyword in lower for keyword in POSTAGE_KEYWORDS):
        return True
    if re.fullmatch(r"(?:PAID|PRESORTED|MAIL|USPS|ZIP)", stripped, re.I):
        return True
    if re.fullmatch(r"[\d\s\-\*\$]+", stripped):
        return True
    return False


def _clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip(" ,.;")
        if not line or _is_noise_line(line):
            continue
        if len(line) < 2:
            continue
        lines.append(line)
    return lines


def _normalize_city_state_zip_line(line: str) -> str:
    """Allow OCR/comma variants like 'Renton, WA 98057'."""
    cleaned = re.sub(r"\s+", " ", line.upper().strip())
    return re.sub(r",\s*([A-Z]{2}\s+\d{5})", r" \1", cleaned)


def _is_city_state_zip(line: str) -> bool:
    return bool(CITY_STATE_ZIP_RE.match(_normalize_city_state_zip_line(line)))


def _is_address_line(line: str) -> bool:
    return bool(ADDRESS_LINE_RE.match(line))


def _is_gibberish(text: str) -> bool:
    cleaned = re.sub(r"[^A-Za-z ]", "", text or "").strip()
    if len(cleaned) < 3:
        return True
    letters = [c for c in cleaned if c.isalpha()]
    if not letters:
        return True
    vowels = sum(1 for c in letters if c.lower() in "aeiou")
    if len(letters) >= 8 and vowels / len(letters) < 0.18:
        return True
    if len(cleaned) >= 20 and vowels / len(letters) < 0.22:
        return True
    words = cleaned.split()
    if len(words) >= 4:
        tiny = sum(1 for word in words if len(word) <= 3)
        if tiny >= max(3, int(len(words) * 0.35)):
            return True
    if re.search(r"[b-df-hj-np-tv-xz]{6,}", cleaned.lower()):
        return True
    for word in cleaned.split():
        if re.fullmatch(r"[A-Z][a-z]+(?:['-][A-Z][a-z]+)?", word):
            continue
        if len(word) >= 5 and not re.search(r"[AEIOUaeiou]", word):
            return True
        internal_upper = sum(1 for c in word[1:] if c.isupper())
        if len(word) >= 8 and internal_upper >= 2:
            return True
    return False


def _is_plausible_name(text: str) -> bool:
    value = (text or "").strip(" ,.;")
    if not value or _is_noise_line(value) or _is_gibberish(value):
        return False
    if _is_city_state_zip(value) or _is_address_line(value):
        return False
    if len(value) > 80:
        return False
    alpha = sum(1 for c in value if c.isalpha())
    return alpha >= max(3, len(value) * 0.45)


def _find_address_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        current.append(line)
        if _is_city_state_zip(line):
            block = current[-5:] if len(current) > 5 else list(current)
            blocks.append(block)
            current = []

    return blocks


def _block_name(block: list[str]) -> str:
    if not block:
        return ""
    for line in block:
        if _is_city_state_zip(line) or _is_address_line(line):
            continue
        if line.upper().startswith("C/O"):
            continue
        if _is_plausible_name(line):
            return line
    for line in block:
        if _is_plausible_name(line):
            return line
    return ""


def _block_address(block: list[str]) -> str:
    if len(block) <= 1:
        return ""
    name = _block_name(block)
    rest = [
        line for line in block
        if line != name and not _is_noise_line(line) and not _is_gibberish(line)
    ]
    return ", ".join(rest)


def _parse_region(text: str) -> dict[str, str]:
    lines = _clean_lines(text or "")
    blocks = _find_address_blocks(lines)
    if blocks:
        block = blocks[-1]
        return {
            "name": _block_name(block),
            "address": _block_address(block),
        }
    name = ""
    address_lines: list[str] = []
    for line in lines:
        if not name and _is_plausible_name(line):
            name = line
            continue
        if name:
            if not (_is_gibberish(line) or _is_noise_line(line)):
                address_lines.append(line)
    return {
        "name": name,
        "address": ", ".join(address_lines),
    }


def _score_parties(parties: dict[str, str]) -> int:
    score = 0
    for key in ("from_name", "to_name"):
        if _is_plausible_name(parties.get(key, "")):
            score += 4
    for key in ("from_address", "to_address"):
        value = parties.get(key, "")
        if value and (_is_city_state_zip(value.split(",")[-1].strip().upper()) or _is_address_line(value)):
            score += 3
        elif value:
            score += 1
    return score


def parse_parties_from_text(text: str, *, ocr_available: bool | None = None) -> dict[str, Any]:
    """Parse sender/recipient from OCR text."""
    lines = _clean_lines(text or "")
    blocks = _find_address_blocks(lines)

    result: dict[str, Any] = {
        "from_name": "",
        "from_address": "",
        "to_name": "",
        "to_address": "",
        "ocr_text": (text or "").strip(),
        "ocr_available": _ocr_available() if ocr_available is None else bool(ocr_available),
    }

    if not blocks:
        plausible = [line for line in lines if _is_plausible_name(line)]
        if plausible:
            result["from_name"] = plausible[0]
            if len(plausible) > 1:
                result["to_name"] = plausible[-1]
        return result

    sender = blocks[0]
    recipient = blocks[-1] if len(blocks) > 1 else blocks[0]

    result["from_name"] = _block_name(sender)
    result["from_address"] = _block_address(sender)
    result["to_name"] = _block_name(recipient)
    result["to_address"] = _block_address(recipient)

    if len(blocks) == 1:
        # Single block on a window envelope is usually the recipient.
        result["from_name"] = ""
        result["from_address"] = ""

    return result


def _merge_region_results(
    return_text: str,
    recipient_text: str,
    fallback_text: str,
) -> dict[str, Any]:
    sender = _parse_region(return_text)
    recipient = _parse_region(recipient_text)
    fallback = parse_parties_from_text(fallback_text)

    merged = {
        "from_name": sender["name"] or fallback.get("from_name", ""),
        "from_address": sender["address"] or fallback.get("from_address", ""),
        "to_name": recipient["name"] or fallback.get("to_name", ""),
        "to_address": recipient["address"] or fallback.get("to_address", ""),
        "ocr_text": fallback_text.strip(),
        "ocr_available": _ocr_available(),
    }

    for field in ("from_name", "to_name"):
        if not _is_plausible_name(merged[field]):
            merged[field] = ""

    if merged["from_name"] and merged["to_name"] and merged["from_name"].lower() == merged["to_name"].lower():
        merged["from_name"] = ""
        merged["from_address"] = ""

    return merged


def parse_letter_parties(img_bytes: bytes) -> dict[str, Any]:
    """Parse sender/recipient from a letter scan image via Tesseract OCR."""
    if not _ocr_available():
        return parse_parties_from_text("", ocr_available=False)

    try:
        from PIL import Image

        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        return_region = _crop_fraction(img, RETURN_REGION)
        recipient_region = _crop_fraction(img, RECIPIENT_REGION)

        spatial = _ocr_spatial(img)
        return_text = _ocr_image(return_region, psm=6)
        recipient_text = _ocr_image(recipient_region, psm=6)

        candidates: list[dict[str, Any]] = [
            _merge_region_results(return_text, recipient_text, spatial),
        ]
        for text in _collect_ocr_candidates(img):
            candidates.append(parse_parties_from_text(text))

        best = max(candidates, key=_score_parties)
        best["ocr_available"] = True
        if not best.get("ocr_text"):
            best["ocr_text"] = spatial
        return best
    except Exception as exc:
        logger.warning("Letter OCR failed: %s", exc)
        return parse_parties_from_text("", ocr_available=_ocr_available())
