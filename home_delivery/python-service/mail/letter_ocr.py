"""
OCR helpers for Informed Delivery letter scans.

Extracts sender (from) and recipient (to) from window-envelope images using
OpenOCR for accurate text detection and recognition.
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

_openocr_engine = None

CITY_STATE_ZIP_RE = re.compile(
    r"^([A-Z][A-Z .'-]+?)\s*,?\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$"
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
    r"|PRSRT\s*STD"
    r"|NONPROFIT\s*ORG"
    r"|AUTO\*?\*?[A-Z]*\s*\d*"
    r"|ECRWSS"
    r"|EDDM"
    r"|STD\s*MAIL"
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
    "nonprofit",
    "ecrwss",
    "eddm",
    "std mail",
)
REF_JUNK_RE = re.compile(
    r"^\d{5,}/\d{5,}|"
    r"^\*?\s*[\d\s-]{8,}\*?$|"
    r"^\d{5,}/\d{5,}/\d{5,}.*$|"
    r"^[A-Z]{0,3}\d{4,}[A-Z0-9]*$|"
    r"^USBP\d+$"
)

RETURN_REGION = (0.0, 0.0, 0.55, 0.42)
RECIPIENT_REGION = (0.0, 0.38, 0.70, 1.0)
POSTAGE_REGION = (0.55, 0.0, 1.0, 0.35)


def _get_openocr_engine():
    """Lazy-load OpenOCR engine."""
    global _openocr_engine
    if _openocr_engine is None:
        try:
            from openocr import OpenOCR
            _openocr_engine = OpenOCR(mode="ocr")
            logger.info("OpenOCR engine initialized")
        except Exception as exc:
            logger.warning("Failed to initialize OpenOCR: %s", exc)
            _openocr_engine = False
    return _openocr_engine if _openocr_engine else None


def _ocr_available() -> bool:
    """Check if OpenOCR is available."""
    try:
        engine = _get_openocr_engine()
        return engine is not None
    except Exception:
        return False


def _preprocess_for_ocr(img: "Image.Image") -> "Image.Image":
    """Preprocess image for OCR."""
    from PIL import Image, ImageOps

    enhanced = enhance_mail_preview(img)
    if enhanced.mode != "RGB":
        enhanced = enhanced.convert("RGB")
    
    if enhanced.width < 1200:
        scale = max(2, int(1200 / max(enhanced.width, 1)))
        enhanced = enhanced.resize(
            (enhanced.width * scale, enhanced.height * scale),
            Image.Resampling.LANCZOS
        )
    
    return enhanced


def _crop_fraction(img: "Image.Image", box: tuple[float, float, float, float]) -> "Image.Image":
    """Crop image by fractional coordinates."""
    w, h = img.size
    left = int(box[0] * w)
    top = int(box[1] * h)
    right = max(left + 1, int(box[2] * w))
    bottom = max(top + 1, int(box[3] * h))
    return img.crop((left, top, right, bottom))


def _ocr_image(img: "Image.Image") -> list[dict[str, Any]]:
    """Run OpenOCR on image and return detected text blocks with positions."""
    engine = _get_openocr_engine()
    if not engine:
        return []
    
    try:
        import numpy as np
        from PIL import Image as PILImage
        
        processed = _preprocess_for_ocr(img)
        img_array = np.array(processed)
        
        result = engine(img_array)
        
        blocks = []
        if result:
            if isinstance(result, list):
                for item in result:
                    if isinstance(item, dict):
                        text = item.get("text", "").strip()
                        box = item.get("box", [])
                        if text:
                            y_pos = box[1] if len(box) > 1 else 0
                            x_pos = box[0] if len(box) > 0 else 0
                            blocks.append({
                                "text": text,
                                "y": y_pos,
                                "x": x_pos,
                                "box": box,
                            })
                    elif isinstance(item, (list, tuple)) and len(item) >= 2:
                        box = item[0] if isinstance(item[0], (list, tuple)) else []
                        text = str(item[1]).strip() if len(item) > 1 else ""
                        if text:
                            y_pos = box[0][1] if box and len(box[0]) > 1 else 0
                            x_pos = box[0][0] if box and len(box[0]) > 0 else 0
                            blocks.append({
                                "text": text,
                                "y": y_pos,
                                "x": x_pos,
                                "box": box,
                            })
            elif isinstance(result, str):
                for line in result.split("\n"):
                    line = line.strip()
                    if line:
                        blocks.append({"text": line, "y": 0, "x": 0, "box": []})
        
        blocks.sort(key=lambda b: (b.get("y", 0), b.get("x", 0)))
        return blocks
        
    except Exception as exc:
        logger.warning("OpenOCR failed: %s", exc)
        return []


def _ocr_region_text(img: "Image.Image", region: tuple[float, float, float, float]) -> str:
    """OCR a specific region and return concatenated text."""
    cropped = _crop_fraction(img, region)
    blocks = _ocr_image(cropped)
    lines = [b["text"] for b in blocks if b.get("text")]
    return "\n".join(lines)


def _is_noise_line(line: str) -> bool:
    """Check if line is noise (postage, reference numbers, etc.)."""
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
    """Clean and filter OCR text lines."""
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
    """Normalize city/state/ZIP line for matching."""
    cleaned = re.sub(r"\s+", " ", line.upper().strip())
    return re.sub(r",\s*([A-Z]{2}\s+\d{5})", r" \1", cleaned)


def _is_city_state_zip(line: str) -> bool:
    """Check if line matches city/state/ZIP pattern."""
    return bool(CITY_STATE_ZIP_RE.match(_normalize_city_state_zip_line(line)))


def _is_address_line(line: str) -> bool:
    """Check if line looks like a street address."""
    return bool(ADDRESS_LINE_RE.match(line))


def _is_gibberish(text: str) -> bool:
    """Check if text appears to be OCR noise/gibberish."""
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
    """Check if text could be a person or company name."""
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
    """Find contiguous address blocks ending with city/state/ZIP."""
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
    """Extract the name from an address block."""
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
    """Extract address lines from a block."""
    if len(block) <= 1:
        return ""
    name = _block_name(block)
    rest = [
        line for line in block
        if line != name and not _is_noise_line(line) and not _is_gibberish(line)
    ]
    return ", ".join(rest)


def _parse_region(text: str) -> dict[str, str]:
    """Parse sender/recipient from OCR text of a region."""
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
    """Score the quality of extracted sender/recipient data."""
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
        result["from_name"] = ""
        result["from_address"] = ""

    return result


def _merge_region_results(
    return_text: str,
    recipient_text: str,
    full_text: str,
) -> dict[str, Any]:
    """Merge OCR results from different regions."""
    sender = _parse_region(return_text)
    recipient = _parse_region(recipient_text)
    fallback = parse_parties_from_text(full_text)

    merged = {
        "from_name": sender["name"] or fallback.get("from_name", ""),
        "from_address": sender["address"] or fallback.get("from_address", ""),
        "to_name": recipient["name"] or fallback.get("to_name", ""),
        "to_address": recipient["address"] or fallback.get("to_address", ""),
        "ocr_text": full_text.strip(),
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
    """Parse sender/recipient from a letter scan image via OpenOCR."""
    if not _ocr_available():
        return parse_parties_from_text("", ocr_available=False)

    try:
        from PIL import Image

        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        
        return_text = _ocr_region_text(img, RETURN_REGION)
        recipient_text = _ocr_region_text(img, RECIPIENT_REGION)
        
        full_blocks = _ocr_image(img)
        w = img.width
        postage_cutoff = int(w * 0.55)
        filtered_blocks = [
            b for b in full_blocks
            if b.get("x", 0) < postage_cutoff
        ]
        full_text = "\n".join(b["text"] for b in filtered_blocks if b.get("text"))

        candidates: list[dict[str, Any]] = [
            _merge_region_results(return_text, recipient_text, full_text),
            parse_parties_from_text(return_text),
            parse_parties_from_text(recipient_text),
            parse_parties_from_text(full_text),
        ]

        best = max(candidates, key=_score_parties)
        best["ocr_available"] = True
        if not best.get("ocr_text"):
            best["ocr_text"] = full_text
        return best
        
    except Exception as exc:
        logger.warning("Letter OCR failed: %s", exc)
        return parse_parties_from_text("", ocr_available=_ocr_available())
