"""
USPS Informed Delivery IMAP scanner.
Ported from Mail-And-Packages integration.
"""
from __future__ import annotations

import base64
import email
import imaplib
import logging
import re
import uuid
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from typing import Any

from data_config import MAIL_IMAGES_DIR, WWW_DIR

logger = logging.getLogger(__name__)

# Known USPS Informed Delivery senders
USPS_SENDERS = [
    "USPSInformedDelivery@usps.gov",
    "USPSInformeddelivery@email.informeddelivery.usps.com",
    "USPSInformeddelivery@informeddelivery.usps.com",
]

# Subject for daily digest and morning notification emails
INFORMED_DELIVERY_SUBJECTS = [
    "Your Daily Digest",
    "COMING TO YOU SOON",
]

# Legacy alias
DIGEST_SUBJECT = INFORMED_DELIVERY_SUBJECTS[0]

# Images to filter out (not actual mail pieces / ads / branding)
FILTER_PATTERNS = [
    "mailerProvidedImage",
    "ra_0",
    "Mail Attachment.txt",
    "logo",
    "header",
    "footer",
    "banner",
    "campaign",
    "localxchange",
    "local-xchange",
    "local_xchange",
    "smartlocker",
    "smart-locker",
    "advert",
    "promo",
    "marketing",
]

# Promotional / non-letter content markers in Informed Delivery HTML
PROMO_HTML_PATTERNS = [
    re.compile(r"campaign-from", re.IGNORECASE),
    re.compile(r"Local\s*XChange", re.IGNORECASE),
    re.compile(r"USPS\s+Smart\s+Lockers?", re.IGNORECASE),
    re.compile(r"mailerProvidedImage", re.IGNORECASE),
    re.compile(r"Learn more about your mail", re.IGNORECASE),
]

# Placeholder image reference in HTML when no scan available
NO_MAILPIECE_PATTERN = re.compile(r"\bimage-no-mailpieces?700\.jpg\b", re.IGNORECASE)

# USPS Informed Delivery package tracking numbers (IMpb / similar)
USPS_TRACKING_RE = re.compile(r"\b(9\d{19,25}|[A-Z]{2}\d{9}US)\b", re.IGNORECASE)
UPS_TRACKING_RE = re.compile(r"\b(1Z[A-Z0-9]{16})\b", re.IGNORECASE)
TRACKING_LABEL_RE = re.compile(
    r"[?&](?:tLabels|qtc_tLabels|tracknum|trknbr)=([0-9A-Za-z]{10,34})",
    re.IGNORECASE,
)

# HTML element IDs used by Informed Delivery digest package subsections
PACKAGE_SECTION_COUNT_IDS = {
    "expected_today": "today-package-item-number",
    "expected_1_2_days": "onetwodays-package-item-number",
    "awaiting_from_sender": "awaiting-package-item-number",
    "outbound": "outbound-package-item-number",
}


def _get_today_imap_date() -> str:
    """Get today's date in IMAP search format (DD-Mon-YYYY)."""
    return datetime.now().strftime("%d-%b-%Y")


def _build_search_query(senders: list[str], subjects: list[str], since_date: str) -> str:
    """Build IMAP search query for Informed Delivery."""
    # OR together multiple senders
    if len(senders) == 1:
        from_clause = f'FROM "{senders[0]}"'
    else:
        from_clause = f'FROM "{senders[0]}"'
        for sender in senders[1:]:
            from_clause = f'(OR {from_clause} FROM "{sender}")'

    subject_clause = f'SUBJECT "{subjects[0]}"'
    for subject in subjects[1:]:
        subject_clause = f'(OR {subject_clause} SUBJECT "{subject}")'

    return f'({from_clause} {subject_clause} SINCE {since_date})'


# Minimum payload size — skip tracking pixels and tiny icons.
MIN_IMAGE_BYTES = 4096


def _delete_mail_files(filenames: list[str] | None) -> None:
    """Remove previously saved mail preview files."""
    for name in filenames or []:
        if not name:
            continue
        path = MAIL_IMAGES_DIR / Path(name).name
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not delete mail image %s: %s", name, exc)


def _is_valid_mail_image(filename: str) -> bool:
    """Check if filename is likely a mail piece image."""
    if not filename:
        return False

    lower = filename.lower()

    # Must be an image
    if not any(lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif"]):
        return False

    # Filter out known non-mail images
    for pattern in FILTER_PATTERNS:
        if pattern.lower() in lower:
            return False

    return True


def _append_unique_image(images: list[bytes], seen: set[int], payload: bytes | None) -> None:
    """Append decoded image bytes once (dedupe by size + leading bytes)."""
    if not payload or len(payload) < MIN_IMAGE_BYTES:
        return
    fingerprint = hash(payload[:512])
    if fingerprint in seen:
        return
    seen.add(fingerprint)
    images.append(payload)


def _strip_promo_html_sections(html: str) -> str:
    """Remove promotional Informed Delivery blocks (campaigns, Local XChange ads)."""
    cleaned = html
    # Drop tables/divs that look like campaign / marketing cards.
    cleaned = re.sub(
        r"(?is)<(table|tr|td|div)[^>]*(?:campaign-from|mailerProvidedImage|Local\s*XChange)[^>]*>.*?</\1>",
        " ",
        cleaned,
    )
    # Drop remaining promo marker lines.
    for pattern in PROMO_HTML_PATTERNS:
        if pattern.search(cleaned):
            cleaned = pattern.sub(" ", cleaned)
    return cleaned


def _html_looks_like_promo_context(snippet: str) -> bool:
    """Return True when surrounding HTML is promotional rather than a letter scan."""
    return any(p.search(snippet) for p in PROMO_HTML_PATTERNS)


def _extract_images_from_html(msg: Message, images: list[bytes], seen: set[int]) -> None:
    """Pull embedded base64 images from the HTML body, skipping promo ads."""
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        raw = part.get_payload(decode=True)
        if not raw:
            continue
        text = _strip_promo_html_sections(raw.decode("utf-8", errors="ignore"))
        for match in re.finditer(
            r"data:image/(?:jpeg|jpg|png);base64,([A-Za-z0-9+/=\s]+)",
            text,
            flags=re.IGNORECASE,
        ):
            start = max(0, match.start() - 500)
            end = min(len(text), match.end() + 500)
            if _html_looks_like_promo_context(text[start:end]):
                continue
            blob = match.group(1).replace("\n", "").replace("\r", "")
            try:
                _append_unique_image(images, seen, base64.b64decode(blob, validate=False))
            except Exception:
                continue


def _extract_images_from_message(msg: Message) -> list[bytes]:
    """Extract real letter scan images from email MIME parts and HTML embeds."""
    images: list[bytes] = []
    seen: set[int] = set()
    html = _get_message_html(msg)

    for part in msg.walk():
        content_type = part.get_content_type()
        if not content_type.startswith("image/"):
            continue

        content_disposition = str(part.get("Content-Disposition", ""))
        filename = part.get_filename() or ""
        content_id = str(part.get("Content-ID", "")).strip("<>")

        # Skip obvious branding / ad assets when a filename is present.
        if filename and not _is_valid_mail_image(filename):
            continue
        if content_id and not _is_valid_mail_image(content_id):
            # Content-IDs like campaign/logo should be skipped even without extension.
            lower_cid = content_id.lower()
            if any(p.lower() in lower_cid for p in FILTER_PATTERNS):
                continue

        # Skip MIME images referenced only from promotional HTML blocks.
        if html and (filename or content_id):
            for token in filter(None, [filename, content_id]):
                for ref in re.finditer(re.escape(token), html, flags=re.IGNORECASE):
                    snippet = html[max(0, ref.start() - 800) : ref.end() + 800]
                    if _html_looks_like_promo_context(snippet):
                        filename = "__promo__"
                        break
                if filename == "__promo__":
                    break
            if filename == "__promo__":
                continue

        # Prefer attachments and inline scans; unnamed inline parts are often mail pieces.
        if filename or "inline" in content_disposition.lower() or "attachment" in content_disposition.lower():
            payload = part.get_payload(decode=True)
            _append_unique_image(images, seen, payload)

    _extract_images_from_html(msg, images, seen)
    return images


def _has_missing_mailpiece_placeholder(msg: Message) -> bool:
    """Check if email HTML indicates a missing mailpiece image."""
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            html = part.get_payload(decode=True)
            if html:
                text = html.decode("utf-8", errors="ignore")
                if NO_MAILPIECE_PATTERN.search(text):
                    return True
    return False


def _get_message_html(msg: Message) -> str:
    """Return the HTML body of an email when available."""
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        raw = part.get_payload(decode=True)
        if raw:
            return raw.decode("utf-8", errors="ignore")
    return ""


def _html_to_text(html: str) -> str:
    """Convert HTML to a whitespace-normalized plain-text string."""
    text = html.replace("&nbsp;", " ")
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _element_inner_text(html: str, id_suffix: str) -> str | None:
    """Return inner text for the first element whose id ends with id_suffix."""
    pattern = re.compile(
        rf'id="[^"]*{re.escape(id_suffix)}"[^>]*>(.*?)</(?:span|div|td|p|a)>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html)
    if not match:
        return None
    return re.sub(r"\s+", " ", _html_to_text(match.group(1))).strip()


def _parse_section_count(html: str, id_suffix: str) -> int | None:
    """Parse an integer count from a digest section badge element."""
    text = _element_inner_text(html, id_suffix)
    if text is None:
        return None
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def _packages_section_html(html: str) -> str:
    """Return the PACKAGES section HTML when present."""
    match = re.search(r'id="[^"]*packages-section"', html, flags=re.IGNORECASE)
    if not match:
        # Fallback: start at the PACKAGES heading text.
        match = re.search(r">\s*PACKAGES\s*<", html, flags=re.IGNORECASE)
        if not match:
            return html
    return html[match.start() :]


def _extract_tracking_numbers_from_html(html: str) -> list[str]:
    """Extract unique tracking numbers from Informed Delivery package links/text."""
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        cleaned = re.sub(r"[\s\-]", "", raw.strip().upper())
        if not cleaned or cleaned in seen:
            return
        # Ignore short / non-tracking noise from Gmail chrome.
        if len(cleaned) < 12:
            return
        seen.add(cleaned)
        found.append(cleaned)

    # Prefer explicit tracking URL params and USPS IMpb numbers.
    for raw in TRACKING_LABEL_RE.findall(html):
        _add(raw)
    for raw in USPS_TRACKING_RE.findall(html):
        _add(raw)

    # UPS numbers only when tied to a UPS tracking URL (avoid random 1Z… in page chrome).
    for match in re.finditer(
        r"ups\.com/track[^\"'\s<>]*tracknum=([0-9A-Za-z]{10,34})",
        html,
        flags=re.IGNORECASE,
    ):
        _add(match.group(1))

    return found


def _count_campaign_items(html: str) -> int:
    """Count promotional campaign cards (Local XChange, etc.)."""
    return len(re.findall(r'id="[^"]*campaign-from-span-id"', html, flags=re.IGNORECASE))


def _parse_delivery_digest(msg: Message) -> dict[str, Any]:
    """
    Parse Informed Delivery digest HTML.

    Package count uses only the Expected Today subsection.
    Tracking numbers are collected from the whole Packages section for auto-discovery.
    """
    html = _get_message_html(msg)
    if not html:
        return {
            "mailpiece_count": None,
            "package_count": None,
            "section_counts": {},
            "tracking_numbers": [],
        }

    packages_html = _packages_section_html(html)
    section_counts: dict[str, int | None] = {}
    for key, suffix in PACKAGE_SECTION_COUNT_IDS.items():
        section_counts[key] = _parse_section_count(html, suffix)

    expected_today = section_counts.get("expected_today")
    tracking_numbers = _extract_tracking_numbers_from_html(packages_html)

    # Mailpiece count: prefer total-mailpieces minus campaign ads.
    mailpiece_count = _parse_section_count(html, "total-mailpieces")
    campaign_count = _count_campaign_items(html)
    if mailpiece_count is not None and campaign_count:
        mailpiece_count = max(0, mailpiece_count - campaign_count)

    if mailpiece_count is None:
        text = _html_to_text(html)
        combined = re.search(
            r"you have\s+(\d+)\s*mail\s*pieces?(?:\(\s*s\s*\))?",
            text,
            flags=re.IGNORECASE,
        )
        if combined:
            mailpiece_count = max(0, int(combined.group(1)) - campaign_count)

    return {
        "mailpiece_count": mailpiece_count,
        "package_count": expected_today if expected_today is not None else 0,
        "section_counts": section_counts,
        "tracking_numbers": tracking_numbers,
    }


def _normalize_folder(folder: str | None) -> str:
    """Return a usable IMAP mailbox name."""
    cleaned = (folder or "").strip()
    if cleaned.startswith('"') and cleaned.endswith('"') and len(cleaned) >= 2:
        cleaned = cleaned[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return cleaned or "INBOX"


# Characters that force IMAP quoting (RFC 3501 atom-specials + space).
_IMAP_QUOTE_CHARS = frozenset(' (){%*"\\]')


def _needs_imap_quote(name: str) -> bool:
    return any(ch in _IMAP_QUOTE_CHARS or ch.isspace() for ch in name)


def _quote_imap_mailbox(name: str) -> str:
    """
    Quote a mailbox name for SELECT/EXAMINE when it is not a simple atom.

    Recent Python imaplib versions pass mailbox names to the server without
    quoting; Gmail rejects ``EXAMINE USPS 443 Linwood`` with BAD parse errors.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return "INBOX"
    if cleaned.startswith('"') and cleaned.endswith('"') and len(cleaned) >= 2:
        return cleaned
    if not _needs_imap_quote(cleaned):
        return cleaned
    escaped = cleaned.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _unquote_imap_string(value: str) -> str:
    """Decode an IMAP quoted string from a LIST/LSUB response."""
    return value.replace("\\\"", '"').replace("\\\\", "\\")


def _parse_list_mailbox(line: bytes | str) -> str | None:
    """Extract mailbox name from an IMAP LIST response line."""
    if isinstance(line, bytes):
        text = line.decode("utf-8", errors="replace")
    else:
        text = line

    # Typical: (\HasNoChildren) "/" "INBOX"
    # Gmail labels: (\HasNoChildren) "/" "USPS 443 Linwood"
    # The hierarchy delimiter is the first quoted string; mailbox is the last.
    quoted = re.findall(r'"((?:\\.|[^"\\])*)"', text)
    if quoted:
        name = _unquote_imap_string(quoted[-1])
        return name or None

    # Unquoted mailbox (e.g. INBOX): *) "/" INBOX
    match = re.search(r"\)\s+\S+\s+(\S+)\s*$", text)
    if match:
        return match.group(1) or None

    return None


def _sort_folders(folders: list[str]) -> list[str]:
    """Sort folders with INBOX first, then alphabetically."""
    unique = sorted({f for f in folders if f}, key=str.lower)
    if "INBOX" in unique:
        unique.remove("INBOX")
        unique.insert(0, "INBOX")
    return unique


def list_imap_folders(
    host: str,
    port: int,
    user: str,
    password: str,
) -> list[str]:
    """
    Connect to IMAP and return selectable mailbox names.

    Raises:
        ValueError: On connection, auth, or LIST failures.
    """
    if not all([host, user, password]):
        raise ValueError("IMAP host, user, and password are required")

    imap: imaplib.IMAP4_SSL | None = None
    try:
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(user, password)

        status, mailboxes = imap.list()
        if status != "OK":
            detail = mailboxes[0].decode("utf-8", errors="replace") if mailboxes and mailboxes[0] else "list failed"
            raise ValueError(f"Failed to list mailboxes: {detail}")

        folders: list[str] = []
        for entry in mailboxes or []:
            if not entry:
                continue
            name = _parse_list_mailbox(entry)
            if name:
                folders.append(name)

        if not folders:
            raise ValueError("No mailboxes found on this account")

        return _sort_folders(folders)
    finally:
        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass


def _select_mailbox(imap: imaplib.IMAP4_SSL, folder: str) -> str:
    """
    Select an IMAP mailbox, falling back to INBOX when needed.

    imaplib does not raise when SELECT fails; callers must verify status
    or SEARCH will fail with 'illegal in state AUTH'.
    """
    candidates = [folder]
    if folder.upper() != "INBOX":
        candidates.append("INBOX")

    last_error = "Unknown mailbox error"
    for candidate in candidates:
        mailbox = _quote_imap_mailbox(candidate)
        status, data = imap.select(mailbox, readonly=True)
        if status == "OK":
            if candidate != folder:
                logger.warning(
                    "IMAP folder '%s' unavailable; using '%s' instead",
                    folder,
                    candidate,
                )
            return candidate

        detail = data[0].decode("utf-8", errors="replace") if data and data[0] else "select failed"
        last_error = detail
        logger.warning("Failed to select IMAP folder '%s': %s", candidate, detail)

    raise ValueError(f"Failed to select mailbox '{folder}': {last_error}")


async def check_informed_delivery(mail_config: dict[str, Any]) -> dict[str, Any]:
    """
    Check IMAP for today's Informed Delivery email.

    Args:
        mail_config: Dict with imap_host, imap_port, imap_user, imap_password, folder.

    Returns:
        Dict with piece_count and gif_filename.
    """
    host = mail_config.get("imap_host", "")
    port = mail_config.get("imap_port", 993)
    user = mail_config.get("imap_user", "")
    password = mail_config.get("imap_password", "")
    folder = _normalize_folder(mail_config.get("folder"))

    if not all([host, user, password]):
        raise ValueError("IMAP credentials not configured")

    logger.info(f"Checking Informed Delivery via IMAP: {host} (folder: {folder})")

    imap: imaplib.IMAP4_SSL | None = None
    try:
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(user, password)
        selected_folder = _select_mailbox(imap, folder)
    except Exception as e:
        logger.error(f"IMAP connection failed: {e}")
        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass
        raise

    try:
        # Search for today's Informed Delivery email
        search_query = _build_search_query(USPS_SENDERS, INFORMED_DELIVERY_SUBJECTS, _get_today_imap_date())
        logger.debug(f"IMAP search in {selected_folder}: {search_query}")

        # Try UTF-8 charset first
        try:
            status, data = imap.search("UTF-8", search_query)
        except Exception:
            status, data = imap.search(None, search_query)

        if status != "OK":
            detail = data[0].decode("utf-8", errors="replace") if data and data[0] else "search failed"
            raise ValueError(f"IMAP search failed: {detail}")

        if not data or not data[0]:
            logger.info("No Informed Delivery email found for today")
            return {
                "piece_count": 0,
                "mailpiece_count": 0,
                "package_count": 0,
                "gif_filename": None,
                "preview_images": [],
                "tracking_numbers": [],
                "section_counts": {},
            }

        # Get the latest matching email
        email_ids = data[0].split()
        latest_id = email_ids[-1]

        status, msg_data = imap.fetch(latest_id, "(RFC822)")
        if status != "OK" or not msg_data[0]:
            logger.warning("Failed to fetch email")
            return {
                "piece_count": 0,
                "mailpiece_count": 0,
                "package_count": 0,
                "gif_filename": None,
                "preview_images": [],
                "tracking_numbers": [],
                "section_counts": {},
            }

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        digest = _parse_delivery_digest(msg)

        # Extract real letter scans only (exclude Local XChange / campaign ads).
        images = _extract_images_from_message(msg)

        if digest["mailpiece_count"] is not None:
            mailpiece_count = digest["mailpiece_count"]
        else:
            if _has_missing_mailpiece_placeholder(msg):
                images.append(b"")
            mailpiece_count = len(images)

        # Only Expected Today packages count toward the dashboard package total.
        package_count = digest.get("package_count") or 0
        tracking_numbers = digest.get("tracking_numbers") or []

        logger.info(
            "Parsed Informed Delivery: %s mailpiece(s), %s Expected Today package(s), "
            "%s tracking number(s) discovered (sections=%s)",
            mailpiece_count,
            package_count,
            len(tracking_numbers),
            digest.get("section_counts"),
        )

        piece_count = mailpiece_count + package_count

        preview_images, gif_filename = await _save_mail_previews(
            mail_config.get("id", "mail"),
            images,
            previous_files=_collect_previous_files(mail_config),
        )

        return {
            "piece_count": piece_count,
            "mailpiece_count": mailpiece_count,
            "package_count": package_count,
            "gif_filename": gif_filename,
            "preview_images": preview_images,
            "tracking_numbers": tracking_numbers,
            "section_counts": digest.get("section_counts") or {},
        }

    finally:
        if imap is not None:
            try:
                imap.close()
            except Exception:
                pass
            try:
                imap.logout()
            except Exception:
                pass


def _collect_previous_files(mail_config: dict[str, Any]) -> list[str]:
    """Return prior preview filenames stored on a mail account."""
    files: list[str] = []
    gif = mail_config.get("gif_filename")
    if gif:
        files.append(gif)
    for name in mail_config.get("preview_images") or []:
        if name and name not in files:
            files.append(name)
    return files


async def _save_mail_previews(
    account_key: str,
    images: list[bytes],
    previous_files: list[str] | None = None,
) -> tuple[list[str], str | None]:
    """
    Persist individual JPEG previews and an optional animated GIF.

    Returns:
        Tuple of (preview_image_filenames, gif_filename).
    """
    _delete_mail_files(previous_files)

    real_images = [img for img in images if img]
    if not real_images:
        return [], None

    from PIL import Image
    from io import BytesIO

    preview_images: list[str] = []
    safe_key = re.sub(r"[^a-zA-Z0-9]", "", str(account_key))[:12] or "mail"
    batch = uuid.uuid4().hex[:8]

    for index, img_bytes in enumerate(real_images):
        try:
            img = Image.open(BytesIO(img_bytes)).convert("RGB")
            img.thumbnail((724, 320), Image.Resampling.LANCZOS)
            filename = f"mail_{safe_key}_{batch}_{index}.jpg"
            output_path = MAIL_IMAGES_DIR / filename
            img.save(output_path, format="JPEG", quality=88, optimize=True)
            preview_images.append(filename)
        except Exception as exc:
            logger.warning("Failed to save mail preview %s: %s", index, exc)

    gif_filename = await _generate_mail_gif(real_images) if preview_images else None
    return preview_images, gif_filename


async def _generate_mail_gif(images: list[bytes]) -> str | None:
    """
    Generate animated GIF from mail piece images.

    Args:
        images: List of image bytes.

    Returns:
        Filename of generated GIF or None.
    """
    try:
        import imageio
        from PIL import Image
        from io import BytesIO

        frames = []
        target_size = (724, 320)

        for img_bytes in images:
            if not img_bytes:
                # Placeholder - create gray rectangle
                img = Image.new("RGB", target_size, color=(200, 200, 200))
            else:
                try:
                    img = Image.open(BytesIO(img_bytes))
                    img = img.convert("RGB")

                    # Resize to fit within target while maintaining aspect ratio
                    img.thumbnail(target_size, Image.Resampling.LANCZOS)

                    # Center on target-sized canvas
                    canvas = Image.new("RGB", target_size, color=(255, 255, 255))
                    offset = ((target_size[0] - img.width) // 2, (target_size[1] - img.height) // 2)
                    canvas.paste(img, offset)
                    img = canvas
                except Exception as e:
                    logger.warning(f"Failed to process image: {e}")
                    continue

            frames.append(img)

        if not frames:
            return None

        # Generate unique filename
        filename = f"mail_{uuid.uuid4().hex[:8]}.gif"
        output_path = MAIL_IMAGES_DIR / filename

        # Convert PIL images to numpy arrays for imageio
        import numpy as np
        np_frames = [np.array(f) for f in frames]

        # Write animated GIF (2 seconds per frame)
        imageio.mimwrite(str(output_path), np_frames, duration=2, loop=0)

        logger.info(f"Generated mail GIF: {filename}")
        return filename

    except ImportError as e:
        logger.error(f"Missing dependency for GIF generation: {e}")
        return None
    except Exception as e:
        logger.error(f"GIF generation failed: {e}")
        return None
