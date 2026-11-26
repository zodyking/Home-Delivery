"""
OCR helpers for Informed Delivery letter scans.

Extracts sender (from) and recipient (to) from the front of a window envelope.
Tesseract is the engine installed in the add-on image. OpenOCR is used only
when Tesseract is missing or returns no words.

Envelope text is almost always ALL CAPS. Party names are accepted in that
form; postage indicia and barcode noise are still dropped.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from io import BytesIO
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL import Image

from mail.image_enhance import enhance_mail_preview

logger = logging.getLogger(__name__)

_openocr_engine: Any = None

# Two-letter token must be a real state. Otherwise "PO BOX 31254" matches
# as city "PO B" / state "OX" / ZIP 31254 and the city line is split off.
_US_STATE = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|"
    "MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|"
    "WI|WY|DC"
)
CITY_STATE_ZIP_RE = re.compile(
    rf"^([A-Z][A-Z .'-]*?)\s*,?\s*({_US_STATE})\s+(\d{{5}}(?:-\d{{4}})?)$"
)
STREET_SUFFIX_RE = (
    r"(?:ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|DR|DRIVE|LN|LANE|CT|COURT|"
    r"WAY|PL|PLACE|PKWY|PARKWAY|HWY|HIGHWAY|CIR|CIRCLE|TER|TERRACE|"
    r"TRL|TRAIL|LOOP|PATH|PIKE|ALY|ALLEY|RUN|XING|SQ|PLZ|PLAZA)"
)
ADDRESS_LINE_RE = re.compile(
    rf"^(?:P\.?O\.?\s*BOX\s+\d+|\d+\s+[A-Z0-9 .#/-]*(?:{STREET_SUFFIX_RE})\b.*)$",
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


def _tesseract_bin() -> str | None:
    return shutil.which("tesseract")


def _get_openocr_engine():
    """Lazy-load OpenOCR. Returns None when the package or models are unavailable."""
    global _openocr_engine
    if _openocr_engine is None:
        try:
            from openocr import OpenOCR

            # task="ocr" + mode="mobile" is the documented CPU path.
            # mode="ocr" is not a valid mode and yields no text.
            _openocr_engine = OpenOCR(task="ocr", mode="mobile")
            logger.info("OpenOCR engine initialized")
        except Exception as exc:
            logger.warning("Failed to initialize OpenOCR: %s", exc)
            _openocr_engine = False
    return _openocr_engine if _openocr_engine else None


def _openocr_installed() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("openocr") is not None
    except Exception:
        return False


def _ocr_available() -> bool:
    """True when Tesseract or OpenOCR can run. Does not load model weights."""
    if _tesseract_bin():
        return True
    if _openocr_engine:
        return True
    if _openocr_engine is False:
        return False
    return _openocr_installed()


def _preprocess_for_ocr(img: "Image.Image") -> "Image.Image":
    """Upscale a washed-out scan so both engines can read small return addresses."""
    from PIL import Image

    enhanced = enhance_mail_preview(img)
    if enhanced.mode != "RGB":
        enhanced = enhanced.convert("RGB")
    if enhanced.width < 1400:
        scale = max(2, int(1400 / max(enhanced.width, 1)))
        enhanced = enhanced.resize(
            (enhanced.width * scale, enhanced.height * scale),
            Image.Resampling.LANCZOS,
        )
    return enhanced


def _blocks_from_tsv(tsv: str) -> list[dict[str, Any]]:
    """Turn Tesseract TSV into word boxes."""
    rows = tsv.splitlines()
    if not rows:
        return []
    header = rows[0].split("\t")
    try:
        level_i = header.index("level")
        left_i = header.index("left")
        top_i = header.index("top")
        width_i = header.index("width")
        height_i = header.index("height")
        conf_i = header.index("conf")
        text_i = header.index("text")
        block_i = header.index("block_num")
        par_i = header.index("par_num")
        line_i = header.index("line_num")
    except ValueError:
        return []

    blocks: list[dict[str, Any]] = []
    for row in rows[1:]:
        cols = row.split("\t")
        if len(cols) <= text_i:
            continue
        if cols[level_i] != "5":
            continue
        text = cols[text_i].strip()
        if not text:
            continue
        try:
            conf = float(cols[conf_i])
        except ValueError:
            conf = -1
        if conf < 35:
            continue
        blocks.append({
            "text": text,
            "x": int(float(cols[left_i] or 0)),
            "y": int(float(cols[top_i] or 0)),
            "h": int(float(cols[height_i] or 0)) or 12,
            "w": int(float(cols[width_i] or 0)),
            "line_key": (cols[block_i], cols[par_i], cols[line_i]),
        })
    return blocks


def _tesseract_blocks(img: "Image.Image") -> list[dict[str, Any]]:
    binary = _tesseract_bin()
    if not binary:
        return []
    processed = _preprocess_for_ocr(img)
    buffer = BytesIO()
    processed.save(buffer, format="PNG")
    try:
        proc = subprocess.run(
            [binary, "stdin", "stdout", "-l", "eng", "--psm", "11", "tsv"],
            input=buffer.getvalue(),
            capture_output=True,
            timeout=40,
            check=False,
        )
    except Exception as exc:
        logger.warning("Tesseract failed: %s", exc)
        return []
    if proc.returncode != 0:
        logger.debug("Tesseract error: %s", proc.stderr[:240])
        return []
    return _blocks_from_tsv(proc.stdout.decode("utf-8", errors="replace"))


def _block_from_openocr_box(box: dict[str, Any], index: int) -> dict[str, Any] | None:
    text = str(box.get("transcription") or box.get("text") or "").strip()
    if not text:
        return None
    points = box.get("points") or box.get("box") or []
    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
    return {
        "text": text,
        "x": min(xs) if xs else 0,
        "y": min(ys) if ys else index * 18,
        "h": (max(ys) - min(ys)) if ys else 16,
        "w": (max(xs) - min(xs)) if xs else 0,
        "line_key": None,
    }


def _openocr_blocks(img: "Image.Image") -> list[dict[str, Any]]:
    engine = _get_openocr_engine()
    if not engine:
        return []
    processed = _preprocess_for_ocr(img)
    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = tmp.name
        processed.save(path, format="PNG")
        result = engine(image_path=path)
        payload = result[0] if isinstance(result, tuple) else result
        items = payload if isinstance(payload, list) else [payload]
        blocks: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                block = _block_from_openocr_box(item, len(blocks))
                if block:
                    blocks.append(block)
                continue
            if not isinstance(item, str):
                continue
            raw = item.split("\t", 1)[1] if "\t" in item else item
            try:
                data = json.loads(raw)
            except Exception:
                text = raw.strip()
                if text:
                    blocks.append({
                        "text": text,
                        "x": 0,
                        "y": len(blocks) * 18,
                        "h": 16,
                        "w": 0,
                        "line_key": None,
                    })
                continue
            if isinstance(data, list):
                for box in data:
                    if isinstance(box, dict):
                        block = _block_from_openocr_box(box, len(blocks))
                        if block:
                            blocks.append(block)
        return blocks
    except Exception as exc:
        logger.warning("OpenOCR failed: %s", exc)
        return []
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _lines_from_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join word boxes into reading-order lines."""
    if not blocks:
        return []
    keyed = [block for block in blocks if block.get("line_key")]
    if keyed and len(keyed) == len(blocks):
        groups: dict[tuple, list[dict[str, Any]]] = {}
        for block in blocks:
            groups.setdefault(block["line_key"], []).append(block)
        lines = []
        for words in groups.values():
            words.sort(key=lambda word: word.get("x", 0))
            lines.append({
                "text": " ".join(word["text"] for word in words if word.get("text")),
                "x": min(word.get("x", 0) for word in words),
                "y": min(word.get("y", 0) for word in words),
                "h": max(word.get("h", 12) for word in words),
            })
        lines.sort(key=lambda line: (line["y"], line["x"]))
        return [line for line in lines if line["text"].strip()]

    rows: list[dict[str, Any]] = []
    for block in sorted(blocks, key=lambda item: (item.get("y", 0), item.get("x", 0))):
        height = block.get("h") or 14
        if rows and abs(block.get("y", 0) - rows[-1]["y"]) <= max(10, height * 0.7):
            rows[-1]["words"].append(block)
            rows[-1]["y"] = min(rows[-1]["y"], block.get("y", 0))
        else:
            rows.append({"y": block.get("y", 0), "words": [block]})
    lines = []
    for row in rows:
        words = sorted(row["words"], key=lambda word: word.get("x", 0))
        text = " ".join(word["text"] for word in words if word.get("text")).strip()
        if not text:
            continue
        lines.append({
            "text": text,
            "x": min(word.get("x", 0) for word in words),
            "y": row["y"],
            "h": max(word.get("h", 12) for word in words),
        })
    return lines


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
    cleaned = re.sub(r"\s+", " ", line.upper().strip())
    return re.sub(r",\s*([A-Z]{2}\s+\d{5})", r" \1", cleaned)


def _is_city_state_zip(line: str) -> bool:
    return bool(CITY_STATE_ZIP_RE.match(_normalize_city_state_zip_line(line)))


def _is_address_line(line: str) -> bool:
    return bool(ADDRESS_LINE_RE.match(line))


def _is_gibberish(text: str) -> bool:
    """
    Reject OCR noise.

    Envelope addresses are printed in capitals, so ALL CAPS is not noise.
    """
    cleaned = re.sub(r"[^A-Za-z ]", "", text or "").strip()
    if len(cleaned) < 3:
        return True
    letters = [char for char in cleaned if char.isalpha()]
    if not letters:
        return True
    sample = cleaned.lower()
    vowels = sum(1 for char in sample if char in "aeiou")
    if len(letters) >= 10 and vowels / len(letters) < 0.15:
        return True
    if re.search(r"[bcdfghjklmnpqrstvwxz]{6,}", sample):
        return True
    if re.search(r"(.)\1{4,}", sample):
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
    alpha = sum(1 for char in value if char.isalpha())
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
        if name and not (_is_gibberish(line) or _is_noise_line(line)):
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
        if not value:
            continue
        tail = value.split(",")[-1].strip().upper()
        if _is_city_state_zip(tail) or _is_city_state_zip(value) or _is_address_line(value):
            score += 3
        else:
            score += 1
    return score


def parse_parties_from_text(text: str, *, ocr_available: bool | None = None) -> dict[str, Any]:
    """Parse sender/recipient from OCR text that already reads top-to-bottom."""
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

    # A single city/state block on a full envelope is the recipient. The
    # return address often has no ZIP and is handled by the region split.
    if len(blocks) == 1:
        result["from_name"] = ""
        result["from_address"] = ""

    return result


def _merge_region_results(
    return_text: str,
    recipient_text: str,
    full_text: str,
) -> dict[str, Any]:
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

    if (
        merged["from_name"]
        and merged["to_name"]
        and merged["from_name"].lower() == merged["to_name"].lower()
    ):
        merged["from_name"] = ""
        merged["from_address"] = ""

    return merged


def _drop_postage(lines: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    kept = []
    for line in lines:
        x = line.get("x") or 0
        y = line.get("y") or 0
        if width and height and x > width * 0.62 and y < height * 0.38:
            if _is_noise_line(line.get("text") or ""):
                continue
            # Indicia lives in the top-right even when the words aren't on the noise list.
            continue
        kept.append(line)
    return kept


def _split_sender_recipient(
    lines: list[dict[str, Any]],
    *,
    width: int,
    height: int,
) -> tuple[str, str]:
    """Split positioned lines at the gap between the return address and the recipient."""
    usable = _drop_postage(lines, width, height)
    usable = [line for line in usable if not _is_noise_line(line.get("text") or "")]
    usable.sort(key=lambda line: (line.get("y", 0), line.get("x", 0)))
    if not usable:
        return "", ""

    gap_at = None
    gap_size = 0
    for index in range(len(usable) - 1):
        y1 = usable[index].get("y", 0) + usable[index].get("h", 0)
        y2 = usable[index + 1].get("y", 0)
        gap = y2 - y1
        midpoint = (y1 + y2) / 2
        if height and (midpoint < height * 0.18 or midpoint > height * 0.78):
            continue
        if gap > gap_size:
            gap_size = gap
            gap_at = index

    min_gap = max(18, int(height * 0.045)) if height else 18
    if gap_at is not None and gap_size >= min_gap:
        upper = usable[: gap_at + 1]
        lower = usable[gap_at + 1 :]
    else:
        cut = height * 0.42 if height else 0
        upper = [line for line in usable if line.get("y", 0) < cut]
        lower = [line for line in usable if line.get("y", 0) >= cut]
        if not upper or not lower:
            # No geometry — treat the whole read as one column.
            text = "\n".join(line["text"] for line in usable)
            return text, text

    return (
        "\n".join(line["text"] for line in upper),
        "\n".join(line["text"] for line in lower),
    )


def parties_from_ocr_lines(
    lines: list[dict[str, Any]],
    *,
    width: int = 0,
    height: int = 0,
    known_recipient: str = "",
    hint: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build from/to parties from positioned OCR lines. Used by tests and both engines."""
    upper, lower = _split_sender_recipient(lines, width=width, height=height)
    full = "\n".join(line.get("text") or "" for line in lines)
    result = _merge_region_results(upper, lower, full)
    return _apply_hints(result, known_recipient=known_recipient, hint=hint)


def _looks_like_street(text: str) -> bool:
    value = (text or "").strip()
    if not value or "@" in value:
        return False
    if _is_address_line(value) or _is_city_state_zip(value):
        return True
    return any(char.isdigit() for char in value) and any(char.isalpha() for char in value)


def _apply_hints(
    result: dict[str, Any],
    *,
    known_recipient: str = "",
    hint: dict[str, str] | None = None,
) -> dict[str, Any]:
    hint = hint or {}
    if not result.get("from_name") and hint.get("from_name") and _is_plausible_name(hint["from_name"]):
        result["from_name"] = hint["from_name"].strip()
    if not result.get("from_address") and hint.get("from_address"):
        result["from_address"] = hint["from_address"].strip()
    if not result.get("to_name") and hint.get("to_name") and _is_plausible_name(hint["to_name"]):
        result["to_name"] = hint["to_name"].strip()

    recipient = (known_recipient or hint.get("to_address") or "").strip()
    if recipient and "@" not in recipient and not result.get("to_address"):
        if _looks_like_street(recipient):
            result["to_address"] = recipient
        elif not result.get("to_name") and _is_plausible_name(recipient):
            result["to_name"] = recipient
    return result


def _parties_from_blocks(
    blocks: list[dict[str, Any]],
    size: tuple[int, int],
    *,
    known_recipient: str = "",
    hint: dict[str, str] | None = None,
) -> dict[str, Any]:
    width, height = size if size else (0, 0)
    lines = _lines_from_blocks(blocks)
    result = parties_from_ocr_lines(
        lines,
        width=width,
        height=height,
        known_recipient=known_recipient,
        hint=hint,
    )
    result["ocr_available"] = True
    return result


def parse_letter_parties(
    img_bytes: bytes,
    *,
    known_recipient: str = "",
    hint: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Parse sender/recipient from a letter scan image."""
    if not img_bytes:
        return _apply_hints(
            parse_parties_from_text("", ocr_available=_ocr_available()),
            known_recipient=known_recipient,
            hint=hint,
        )

    try:
        from PIL import Image

        img = Image.open(BytesIO(img_bytes)).convert("RGB")
    except Exception as exc:
        logger.warning("Letter image could not be opened: %s", exc)
        return _apply_hints(
            parse_parties_from_text("", ocr_available=_ocr_available()),
            known_recipient=known_recipient,
            hint=hint,
        )

    tesseract_blocks = _tesseract_blocks(img)
    # OpenOCR downloads a model and is slow. The add-on image already has
    # Tesseract; only fall through when that read comes back empty.
    if tesseract_blocks or not _get_openocr_engine():
        result = _parties_from_blocks(
            tesseract_blocks,
            img.size,
            known_recipient=known_recipient,
            hint=hint,
        )
        result["ocr_available"] = _ocr_available()
        return result

    result = _parties_from_blocks(
        _openocr_blocks(img),
        img.size,
        known_recipient=known_recipient,
        hint=hint,
    )
    result["ocr_available"] = True
    return result
