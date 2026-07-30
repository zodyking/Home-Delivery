"""
OCR helpers for Informed Delivery letter scans.

Extracts sender (from) and recipient (to) from window-envelope images.
"""
from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import Any

logger = logging.getLogger(__name__)

# US city / state / ZIP line, e.g. BROOKLYN NY 11208 or FARIBAULT MN 55021-9096
CITY_STATE_ZIP_RE = re.compile(
    r"^([A-Z][A-Z .'-]+?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$"
)
# PO BOX / street-ish lines
ADDRESS_LINE_RE = re.compile(
    r"^(?:PO\s*BOX\s+\d+|P\.?O\.?\s*BOX\s+\d+|\d+\s+.+\s+(?:ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|DR|DRIVE|LN|LANE|CT|COURT|WAY|PL|PLACE|#\S+).*)",
    re.IGNORECASE,
)
NOISE_RE = re.compile(
    r"^(?:"
    r"UAC(?:\s*[-:]?\s*\d+)?"
    r"|FIRST[- ]CLASS(?:\s+MAIL)?"
    r"|PRESORTED"
    r"|U\.?S\.?\s*POSTAGE(?:\s+PAID)?"
    r"|MAIL"
    r"|RIS"
    r"|\d{5}\$\d+(?:\s+C\d+)?"
    r"|C\d{3}"
    r")$",
    re.IGNORECASE,
)
REF_JUNK_RE = re.compile(
    r"^\d{5,}/\d{5,}|"
    r"^\*?\s*[\d\s-]{8,}\*?$|"
    r"^\d{5,}/\d{5,}/\d{5,}.*$|"
    r"^[A-Z]{0,3}\d{4,}[A-Z0-9]*$"  # e.g. VE5876 job codes
)


def _ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


def _preprocess_for_ocr(img_bytes: bytes):
    from PIL import Image, ImageOps, ImageFilter

    img = Image.open(BytesIO(img_bytes)).convert("L")
    # Upscale small scans for better OCR
    if img.width < 900:
        scale = max(2, int(900 / max(img.width, 1)))
        img = img.resize((img.width * scale, img.height * scale), Image.Resampling.LANCZOS)
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.SHARPEN)
    return img


def _ocr_text(img_bytes: bytes) -> str:
    if not _ocr_available():
        return ""
    try:
        import pytesseract

        img = _preprocess_for_ocr(img_bytes)
        # Window envelopes are mostly left-aligned text.
        text = pytesseract.image_to_string(img, config="--psm 6")
        return text or ""
    except Exception as exc:
        logger.warning("Letter OCR failed: %s", exc)
        return ""


def _clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if NOISE_RE.match(line):
            continue
        if REF_JUNK_RE.match(line):
            continue
        if len(line) < 2:
            continue
        # Drop pure barcode-ish leftovers
        if re.fullmatch(r"[\d\s\-\*\$]+", line):
            continue
        lines.append(line)
    return lines


def _is_city_state_zip(line: str) -> bool:
    return bool(CITY_STATE_ZIP_RE.match(line.upper()))


def _find_address_blocks(lines: list[str]) -> list[list[str]]:
    """
    Group OCR lines into address blocks ending with CITY ST ZIP.
    """
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        upper = line.upper()
        current.append(line)
        if _is_city_state_zip(upper):
            # Keep a compact trailing window (name + street + city)
            block = current[-4:] if len(current) > 4 else list(current)
            blocks.append(block)
            current = []

    return blocks


def _block_name(block: list[str]) -> str:
    if not block:
        return ""
    # Prefer first non-address line as name/org
    for line in block:
        if _is_city_state_zip(line.upper()):
            continue
        if ADDRESS_LINE_RE.match(line):
            continue
        if line.upper().startswith("C/O ") or line.upper().startswith("C/O"):
            continue
        if NOISE_RE.match(line) or REF_JUNK_RE.match(line):
            continue
        return line
    return block[0]


def _block_address(block: list[str]) -> str:
    if len(block) <= 1:
        return ""
    name = _block_name(block)
    rest = [line for line in block if line != name]
    return ", ".join(rest)


def parse_parties_from_text(text: str, *, ocr_available: bool | None = None) -> dict[str, Any]:
    """
    Parse sender/recipient from OCR (or sample) text.

    Window envelopes typically list sender first, recipient last.
    """
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
        # Fallback: first meaningful line = from, last remaining line = to
        if lines:
            result["from_name"] = lines[0]
        if len(lines) >= 2:
            result["to_name"] = lines[-1]
        return result

    sender = blocks[0]
    recipient = blocks[-1] if len(blocks) > 1 else blocks[0]

    result["from_name"] = _block_name(sender)
    result["from_address"] = _block_address(sender)
    result["to_name"] = _block_name(recipient)
    result["to_address"] = _block_address(recipient)

    # If only one block, treat it as recipient (common when return address is faint)
    if len(blocks) == 1:
        result["from_name"] = ""
        result["from_address"] = ""

    return result


def parse_letter_parties(img_bytes: bytes) -> dict[str, Any]:
    """Parse sender/recipient from a letter scan image via Tesseract OCR."""
    text = _ocr_text(img_bytes)
    return parse_parties_from_text(text)
