"""
USPS Informed Delivery IMAP scanner.
Ported from Mail-And-Packages integration.
"""
from __future__ import annotations

import base64
import calendar
import email
import imaplib
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from data_config import MAIL_IMAGES_DIR, WWW_DIR
from mail.image_enhance import PREVIEW_MAX_SIZE, enhance_mail_preview, prepare_preview_bytes

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

# Images to filter out (not actual mail pieces / ads / branding).
# Local XChange / campaign ads typically use filenames like mailer-1201990448.jpg
FILTER_PATTERNS = [
    "mailerprovidedimage",
    "mailer-",
    "mailer_",
    "ra_0",
    "mail attachment.txt",
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
    "content-",  # campaign creative assets (content-1201930673.jpg)
]

# Promotional / non-letter content markers in Informed Delivery HTML
PROMO_HTML_PATTERNS = [
    re.compile(r"campaign-from", re.IGNORECASE),
    re.compile(r"campaign-representative", re.IGNORECASE),
    re.compile(r"mail-campaign", re.IGNORECASE),
    re.compile(r"Local\s*XChange", re.IGNORECASE),
    re.compile(r"USPS\s+Smart\s+Lockers?", re.IGNORECASE),
    re.compile(r"mailerProvidedImage", re.IGNORECASE),
    re.compile(r"mailer-\d+", re.IGNORECASE),
    re.compile(r"Learn more about your mail", re.IGNORECASE),
    re.compile(r'alt=["\']campaign["\']', re.IGNORECASE),
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

# Subjects that are an Informed Delivery digest (substring, case-insensitive).
_DIGEST_SUBJECT_RE = re.compile(
    r"daily digest|informed delivery|coming to you|mailbox soon",
    re.IGNORECASE,
)
# USPS subjects use short weekdays ("Thu, Nov 26"), not only "Thursday".
_WEEKDAY_RE = (
    r"(?:Mon(?:day)?|Tue(?:s(?:day)?)?|Wed(?:nes(?:day)?)?|"
    r"Thu(?:r(?:s(?:day)?)?)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)"
)
_MONTH_RE = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
# "Your Daily Digest for Sat, 11/21" or "... 11/21/2026"
DIGEST_SUBJECT_MDY_RE = re.compile(
    rf"Daily Digest(?:\s+for)?\s+{_WEEKDAY_RE},?\s+(\d{{1,2}})/(\d{{1,2}})(?:/(\d{{2,4}}))?",
    re.IGNORECASE,
)
# "Your Daily Digest for Sat, Nov 21" or "... November 26, 2026"
DIGEST_SUBJECT_MONTH_RE = re.compile(
    rf"Daily Digest(?:\s+for)?\s+{_WEEKDAY_RE},?\s+({_MONTH_RE})\.?\s+"
    rf"(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?",
    re.IGNORECASE,
)
BODY_DELIVERIES_RE = re.compile(
    r"Today'?s Deliveries:\s*(\d{1,2})/(\d{1,2})/(\d{2,4})",
    re.IGNORECASE,
)
BODY_LONG_DATE_RE = re.compile(
    rf"\b{_WEEKDAY_RE},?\s+(?:(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RE})\.?|"
    rf"({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?)(?:,?\s+(\d{{4}}))?",
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


def _build_search_query(
    senders: list[str],
    subjects: list[str],
    since_date: str,
    *,
    ignore_sender: bool = False,
) -> str:
    """Build IMAP search query for Informed Delivery."""
    subject_clause = f'SUBJECT "{subjects[0]}"'
    for subject in subjects[1:]:
        subject_clause = f'(OR {subject_clause} SUBJECT "{subject}")'

    if ignore_sender:
        return f'({subject_clause} SINCE {since_date})'

    # OR together multiple senders
    if len(senders) == 1:
        from_clause = f'FROM "{senders[0]}"'
    else:
        from_clause = f'FROM "{senders[0]}"'
        for sender in senders[1:]:
            from_clause = f'(OR {from_clause} FROM "{sender}")'

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

    # Must be an image (Content-IDs without extension are handled separately)
    has_image_ext = any(lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif"])

    # Filter out known non-mail / campaign images (e.g. mailer-1201990448.jpg)
    for pattern in FILTER_PATTERNS:
        if pattern in lower:
            return False

    if not has_image_ext:
        return False

    return True


# Real letter scans from Informed Delivery usually look like 1007217285-018.jpg
LETTER_SCAN_NAME_RE = re.compile(r"^\d{6,}-\d{2,4}\.(jpe?g|png)$", re.IGNORECASE)


def _append_unique_image(
    images: list[dict[str, Any]],
    seen: set[int],
    payload: bytes | None,
    *,
    name: str = "",
) -> None:
    """Append decoded image bytes once (dedupe by size + leading bytes)."""
    if not payload or len(payload) < MIN_IMAGE_BYTES:
        return
    fingerprint = hash(payload[:512])
    if fingerprint in seen:
        return
    seen.add(fingerprint)
    images.append({"bytes": payload, "name": name or ""})


def _image_payloads(images: list[dict[str, Any]]) -> list[bytes]:
    return [img["bytes"] for img in images if img.get("bytes")]


def _select_mailpiece_images(
    images: list[dict[str, Any]],
    mailpiece_count: int | None,
) -> list[bytes]:
    """
    Choose preview images that match the mailpiece count.

    Prefer real letter-scan filenames (NNNNNN-018.jpg) over campaign creatives
    (mailer-*.jpg) when extras remain after filtering.
    """
    if mailpiece_count is not None and mailpiece_count <= 0:
        return []
    if not images:
        return []

    letter_like = [
        img for img in images
        if LETTER_SCAN_NAME_RE.match(Path(img.get("name") or "").name)
    ]
    other = [img for img in images if img not in letter_like]

    # Prefer letter scans, then any remaining non-promo images.
    ordered = letter_like + other
    if mailpiece_count is None:
        return ordered

    if len(ordered) > mailpiece_count:
        logger.info(
            "Selecting %s of %s extracted image(s) for mailpiece previews "
            "(%s letter-scan name matches)",
            mailpiece_count,
            len(ordered),
            len(letter_like),
        )
        # If we have enough named letter scans, use only those.
        if len(letter_like) >= mailpiece_count:
            ordered = letter_like
        # Campaign creatives are usually listed before letter scans — prefer the
        # trailing images when names are missing.
        elif len(letter_like) == 0:
            ordered = ordered[-mailpiece_count:]
        ordered = ordered[:mailpiece_count]

    return ordered


def _image_bytes(image: dict[str, Any] | bytes) -> bytes:
    if isinstance(image, dict):
        return image.get("bytes") or b""
    return image or b""


def _sender_hint_for_image(html: str, name: str) -> str:
    """
    Sender printed next to a mailpiece image.

    Campaign FROM labels sit on the ad image that follows them. A letter scan
    only inherits a FROM label when no other image is between the label and
    this scan.
    """
    if not html or not name:
        return ""
    haystack = html.lower()
    idx = haystack.find(name.lower())
    if idx < 0:
        return ""
    window = html[max(0, idx - 1200) : idx]
    from_at = window.lower().rfind("from:")
    if from_at < 0:
        from_at = window.lower().rfind(">from<")
    if from_at < 0:
        return ""
    between = window[from_at:]
    if re.search(r"<img\b", between, flags=re.IGNORECASE):
        return ""
    text = _html_to_text(between)
    match = re.search(r"\bFROM:?\s+(.+)$", text, flags=re.IGNORECASE)
    if not match:
        return ""
    candidate = match.group(1).strip(" :-")
    if not candidate or len(candidate) > 80:
        return ""
    if re.search(r"mailpiece|campaign|informed delivery", candidate, flags=re.IGNORECASE):
        return ""
    return candidate


def _strip_promo_html_sections(html: str) -> str:
    """Remove promotional Informed Delivery blocks (campaigns, Local XChange ads)."""
    cleaned = html
    # Drop the entire mail-campaign / Local XChange card blocks first.
    cleaned = re.sub(
        r'(?is)<div[^>]*id="[^"]*mail-campaign"[^>]*>.*?</div>',
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?is)<(table|tr|td|div)[^>]*(?:campaign-from|campaign-representative|mailerProvidedImage|Local\s*XChange|mailer-\d+)[^>]*>.*?</\1>",
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


def _extract_images_from_html(
    msg: Message,
    images: list[dict[str, Any]],
    seen: set[int],
) -> None:
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
            start = max(0, match.start() - 800)
            end = min(len(text), match.end() + 800)
            if _html_looks_like_promo_context(text[start:end]):
                continue
            blob = match.group(1).replace("\n", "").replace("\r", "")
            try:
                _append_unique_image(
                    images,
                    seen,
                    base64.b64decode(blob, validate=False),
                    name="embedded.jpg",
                )
            except Exception:
                continue


def _extract_images_from_message(msg: Message) -> list[dict[str, Any]]:
    """Extract real letter scan images from email MIME parts and HTML embeds."""
    images: list[dict[str, Any]] = []
    seen: set[int] = set()
    html = _get_message_html(msg)
    # Work against promo-stripped HTML so CID lookups don't match campaign cards.
    html_clean = _strip_promo_html_sections(html) if html else ""

    for part in msg.walk():
        content_type = part.get_content_type()
        if not content_type.startswith("image/"):
            continue

        content_disposition = str(part.get("Content-Disposition", ""))
        filename = part.get_filename() or ""
        content_id = str(part.get("Content-ID", "")).strip("<>")
        display_name = filename or content_id

        # Skip obvious branding / ad assets (mailer-*.jpg, campaign, etc.).
        if filename and not _is_valid_mail_image(filename):
            logger.debug("Skipping promo/non-mail image filename: %s", filename)
            continue
        if content_id:
            lower_cid = content_id.lower()
            if any(p in lower_cid for p in FILTER_PATTERNS):
                logger.debug("Skipping promo/non-mail Content-ID: %s", content_id)
                continue

        # Skip MIME images referenced from promotional HTML blocks.
        is_promo = False
        if html and (filename or content_id):
            for token in filter(None, [filename, content_id]):
                for ref in re.finditer(re.escape(token), html, flags=re.IGNORECASE):
                    snippet = html[max(0, ref.start() - 1200) : ref.end() + 1200]
                    if _html_looks_like_promo_context(snippet):
                        is_promo = True
                        break
                if is_promo:
                    break
        if is_promo:
            logger.debug("Skipping image referenced in promo HTML: %s", display_name)
            continue

        # Prefer attachments and inline scans; unnamed inline parts are often mail pieces.
        if filename or "inline" in content_disposition.lower() or "attachment" in content_disposition.lower():
            # If HTML still exists after promo strip and this CID is only in stripped
            # campaign HTML, skip it.
            if content_id and html_clean and content_id not in html_clean and content_id in html:
                logger.debug("Skipping image only present in campaign HTML: %s", content_id)
                continue
            payload = part.get_payload(decode=True)
            _append_unique_image(images, seen, payload, name=display_name)

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


def _header_text(value: Any) -> str:
    """Decode an RFC 2047 header into a plain string."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def _local_today() -> date:
    """Calendar day for the household. Uses TZ when Home Assistant sets it."""
    tz_name = (os.environ.get("TZ") or "").strip()
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(tz_name)).date()
        except Exception:
            pass
    return datetime.now().astimezone().date()


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None


def _month_number(name: str) -> int | None:
    key = (name or "").strip(".").lower()[:3]
    for index, abbr in enumerate(calendar.month_abbr):
        if abbr and abbr.lower() == key:
            return index
    return None


def _infer_year(month: int, day: int, anchor: date) -> date | None:
    """Pick the year that lands on the digest day, not the following UTC day."""
    candidates: list[date] = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        parsed = _safe_date(year, month, day)
        if parsed:
            candidates.append(parsed)
    if not candidates:
        return None

    def _rank(value: date) -> tuple[int, int]:
        delta = (value - anchor).days
        # USPS often sends the digest the evening before the delivery day.
        close = 0 if -1 <= delta <= 2 else 50 + abs(delta)
        return (close, abs(delta))

    return min(candidates, key=_rank)


def _header_civil_date(msg: Message) -> date | None:
    """
    Date written on the message, in the offset the sender used.

    Converting that instant to the container clock (usually UTC) moves a
    late-evening US digest onto the next calendar day for every address.
    """
    raw = _header_text(msg.get("Date"))
    if not raw:
        return None
    raw = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()
    try:
        return parsedate_to_datetime(raw).date()
    except Exception:
        return None


def _date_from_digest_text(subject: str, body_head: str, anchor: date) -> date | None:
    """Prefer the delivery day USPS prints over the SMTP timestamp."""
    deliveries = BODY_DELIVERIES_RE.search(f"{subject}\n{body_head}")
    if deliveries:
        month, day, year = deliveries.groups()
        year_n = int(year)
        if year_n < 100:
            year_n += 2000
        parsed = _safe_date(year_n, int(month), int(day))
        if parsed:
            return parsed

    numeric = DIGEST_SUBJECT_MDY_RE.search(subject)
    if numeric:
        month, day, year = numeric.groups()
        if year:
            year_n = int(year)
            if year_n < 100:
                year_n += 2000
            parsed = _safe_date(year_n, int(month), int(day))
        else:
            parsed = _infer_year(int(month), int(day), anchor)
        if parsed:
            return parsed

    named = DIGEST_SUBJECT_MONTH_RE.search(subject)
    if named:
        month_name, day, year = named.groups()
        month = _month_number(month_name)
        if month:
            parsed = (
                _safe_date(int(year), month, int(day))
                if year
                else _infer_year(month, int(day), anchor)
            )
            if parsed:
                return parsed

    long_body = BODY_LONG_DATE_RE.search(body_head)
    if long_body:
        day_a, month_a, month_b, day_b, year = long_body.groups()
        if day_a and month_a:
            day, month_name = day_a, month_a
        else:
            day, month_name = day_b, month_b
        month = _month_number(month_name or "")
        if month and day:
            parsed = (
                _safe_date(int(year), month, int(day))
                if year
                else _infer_year(month, int(day), anchor)
            )
            if parsed:
                return parsed

    for match in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", body_head):
        parsed = _safe_date(int(match.group(3)), int(match.group(1)), int(match.group(2)))
        if parsed and abs((parsed - anchor).days) <= 14:
            return parsed
    return None


def message_delivery_date(msg: Message) -> str:
    """YYYY-MM-DD of the mail day this digest is for."""
    anchor = _header_civil_date(msg) or _local_today()
    subject = _header_text(msg.get("Subject"))
    html = _get_message_html(msg)
    body_head = _html_to_text(html)[:2500] if html else ""
    parsed = _date_from_digest_text(subject, body_head, anchor)
    return (parsed or anchor).isoformat()


def _message_delivery_date(msg: Message) -> str:
    """Return YYYY-MM-DD for an Informed Delivery message."""
    return message_delivery_date(msg)


def _is_digest_message(msg: Message) -> bool:
    """True for an Informed Delivery digest, including forwarded copies."""
    subject = _header_text(msg.get("Subject"))
    if _DIGEST_SUBJECT_RE.search(subject):
        return True
    sender = _header_text(msg.get("From")).lower()
    if any(known.lower() in sender for known in USPS_SENDERS):
        html = _get_message_html(msg).lower()
        if any(token in html for token in ("mailpiece", "informed delivery", "daily digest")):
            return True
    return False


def _process_informed_delivery_message(msg: Message) -> dict[str, Any]:
    """Parse counts, tracking numbers, and letter-scan images from one digest."""
    digest = _parse_delivery_digest(msg)
    extracted = _extract_images_from_message(msg)

    if digest["mailpiece_count"] is not None:
        mailpiece_count = digest["mailpiece_count"]
    else:
        mailpiece_count = len(extracted)
        if _has_missing_mailpiece_placeholder(msg) and mailpiece_count == 0:
            mailpiece_count = 0

    images = _select_mailpiece_images(extracted, mailpiece_count)
    html = _get_message_html(msg)
    for image in images:
        hint = _sender_hint_for_image(html, str(image.get("name") or ""))
        if hint:
            image["from_name"] = hint
    package_count = digest.get("package_count") or 0
    tracking_numbers = digest.get("tracking_numbers") or []

    return {
        "date": _message_delivery_date(msg),
        "mailpiece_count": mailpiece_count,
        "package_count": package_count,
        "piece_count": mailpiece_count + package_count,
        "images": images,
        "tracking_numbers": tracking_numbers,
        "section_counts": digest.get("section_counts") or {},
    }


def _connect_imap(mail_config: dict[str, Any]) -> tuple[imaplib.IMAP4_SSL, str]:
    host = mail_config.get("imap_host", "")
    port = mail_config.get("imap_port", 993)
    user = mail_config.get("imap_user", "")
    password = mail_config.get("imap_password", "")
    folder = _normalize_folder(mail_config.get("folder"))

    if not all([host, user, password]):
        raise ValueError("IMAP credentials not configured")

    imap = imaplib.IMAP4_SSL(host, port)
    imap.login(user, password)
    selected = _select_mailbox(imap, folder)
    return imap, selected


def _should_ignore_sender(mail_config: dict[str, Any]) -> bool:
    """
    Skip USPS FROM filter when digests are forwarded into a label/folder.

    Explicit ignore_sender wins; otherwise non-INBOX folders default to True
    so existing custom-folder accounts work without re-saving.
    """
    if "ignore_sender" in mail_config:
        return bool(mail_config.get("ignore_sender"))
    folder = _normalize_folder(mail_config.get("folder"))
    return folder.upper() != "INBOX"


def _imap_search(
    imap: imaplib.IMAP4_SSL,
    since_date: str,
    *,
    ignore_sender: bool = False,
) -> list[bytes]:
    """
    Find Informed Delivery messages on or after since_date.

    Gmail's IMAP server drops or truncates nested OR queries (often down to a
    single recent hit). Search each sender, and the digest subject, with a
    plain SINCE key and union the ids.
    """
    if ignore_sender:
        queries = [
            f'(SINCE {since_date} SUBJECT "Digest")',
            f'(SINCE {since_date} SUBJECT "Informed Delivery")',
        ]
    else:
        queries = [f'(FROM "{sender}" SINCE {since_date})' for sender in USPS_SENDERS]
        queries.append(f'(SUBJECT "Daily Digest" SINCE {since_date})')

    found: list[bytes] = []
    seen: set[bytes] = set()
    any_ok = False
    last_error = "search failed"
    for query in queries:
        try:
            status, data = imap.search(None, query)
        except Exception as exc:
            last_error = str(exc)
            logger.debug("IMAP search failed for %s: %s", query, exc)
            continue
        if status != "OK":
            detail = data[0].decode("utf-8", errors="replace") if data and data[0] else "search failed"
            last_error = detail
            continue
        any_ok = True
        if not data or not data[0]:
            continue
        for email_id in data[0].split():
            if email_id in seen:
                continue
            seen.add(email_id)
            found.append(email_id)

    if not any_ok:
        raise ValueError(f"IMAP search failed: {last_error}")
    return found


def _fetch_message(imap: imaplib.IMAP4_SSL, email_id: bytes) -> Message | None:
    status, msg_data = imap.fetch(email_id, "(RFC822)")
    if status != "OK" or not msg_data or not msg_data[0]:
        return None
    raw_email = msg_data[0][1]
    return email.message_from_bytes(raw_email)


def _known_recipient(mail_config: dict[str, Any] | None) -> str:
    """Street label of the mail account, used when the envelope read misses it."""
    if not mail_config:
        return ""
    label = str(mail_config.get("label") or "").strip()
    if not label or "@" in label:
        return ""
    return label


async def _save_history_letter_images(
    account_key: str,
    day: str,
    images: list[Any],
    *,
    known_recipient: str = "",
) -> list[dict[str, Any]]:
    """Save letter images for a history day and OCR from/to parties."""
    from mail.letter_ocr import parse_letter_parties

    letters: list[dict[str, Any]] = []
    safe_key = re.sub(r"[^a-zA-Z0-9]", "", str(account_key))[:12] or "mail"
    day_key = day.replace("-", "")
    prefix = f"hist_{safe_key}_{day_key}_"

    # Drop prior files for this account/day so Refresh doesn't orphan images.
    try:
        for path in MAIL_IMAGES_DIR.glob(f"{prefix}*.jpg"):
            path.unlink(missing_ok=True)
    except Exception as cleanup_exc:
        logger.debug("History image cleanup skipped: %s", cleanup_exc)

    for index, image in enumerate(images):
        img_bytes = _image_bytes(image)
        if not img_bytes:
            continue
        hint = {}
        if isinstance(image, dict) and image.get("from_name"):
            hint["from_name"] = image["from_name"]
        try:
            parties = parse_letter_parties(
                img_bytes,
                known_recipient=known_recipient,
                hint=hint or None,
            )
            preview_bytes = prepare_preview_bytes(img_bytes)
            filename = f"{prefix}{index}_{uuid.uuid4().hex[:6]}.jpg"
            output_path = MAIL_IMAGES_DIR / filename
            output_path.write_bytes(preview_bytes)
            letters.append({
                "image": filename,
                "account_id": account_key,
                "from_name": parties.get("from_name") or "",
                "from_address": parties.get("from_address") or "",
                "to_name": parties.get("to_name") or "",
                "to_address": parties.get("to_address") or "",
            })
        except Exception as exc:
            logger.warning("Failed to save/OCR history letter %s/%s: %s", day, index, exc)

    return letters


async def check_informed_delivery(mail_config: dict[str, Any]) -> dict[str, Any]:
    """
    Check IMAP for today's Informed Delivery email.

    Args:
        mail_config: Dict with imap_host, imap_port, imap_user, imap_password, folder.

    Returns:
        Dict with piece_count and gif_filename.
    """
    logger.info(
        "Checking Informed Delivery via IMAP: %s (folder: %s)",
        mail_config.get("imap_host"),
        _normalize_folder(mail_config.get("folder")),
    )

    imap: imaplib.IMAP4_SSL | None = None
    try:
        imap, selected_folder = _connect_imap(mail_config)
    except Exception as e:
        logger.error(f"IMAP connection failed: {e}")
        raise

    ignore_sender = _should_ignore_sender(mail_config)

    def _empty_today() -> dict[str, Any]:
        return {
            "piece_count": 0,
            "mailpiece_count": 0,
            "package_count": 0,
            "gif_filename": None,
            "preview_images": [],
            "tracking_numbers": [],
            "section_counts": {},
            "date": _local_today().isoformat(),
            "letters": [],
        }

    try:
        # Look back one day so a UTC container still sees a digest stamped
        # with the household's previous evening offset.
        since = (_local_today() - timedelta(days=1)).strftime("%d-%b-%Y")
        email_ids = _imap_search(
            imap,
            since,
            ignore_sender=ignore_sender,
        )
        logger.debug(
            "IMAP search in %s found %s message(s) (ignore_sender=%s)",
            selected_folder,
            len(email_ids),
            ignore_sender,
        )

        parsed_rows: list[dict[str, Any]] = []
        for email_id in email_ids:
            msg = _fetch_message(imap, email_id)
            if not msg or not _is_digest_message(msg):
                continue
            parsed_rows.append(_process_informed_delivery_message(msg))

        today_key = _local_today().isoformat()
        yesterday_key = (_local_today() - timedelta(days=1)).isoformat()
        todays = [row for row in parsed_rows if row["date"] == today_key]
        if todays:
            parsed = max(todays, key=lambda row: len(row["images"]))
        else:
            yesterdays = [row for row in parsed_rows if row["date"] == yesterday_key]
            parsed = max(yesterdays, key=lambda row: len(row["images"])) if yesterdays else None

        if not parsed:
            logger.info("No Informed Delivery email found for today")
            return _empty_today()
        logger.info(
            "Parsed Informed Delivery: %s mailpiece(s), %s Expected Today package(s), "
            "%s tracking number(s) discovered, %s preview image(s)",
            parsed["mailpiece_count"],
            parsed["package_count"],
            len(parsed["tracking_numbers"]),
            len(parsed["images"]),
        )

        preview_images, gif_filename = await _save_mail_previews(
            mail_config.get("id", "mail"),
            parsed["images"],
            previous_files=_collect_previous_files(mail_config),
        )

        # OCR today's letters for history + UI enrichment (reuse carousel files)
        from mail.letter_ocr import parse_letter_parties

        letters = []
        account_key = mail_config.get("id", "mail")
        recipient = _known_recipient(mail_config)
        for index, image in enumerate(parsed["images"]):
            hint = {}
            if isinstance(image, dict) and image.get("from_name"):
                hint["from_name"] = image["from_name"]
            parties = parse_letter_parties(
                _image_bytes(image),
                known_recipient=recipient,
                hint=hint or None,
            )
            letters.append({
                "image": preview_images[index] if index < len(preview_images) else "",
                "account_id": account_key,
                "from_name": parties.get("from_name") or "",
                "from_address": parties.get("from_address") or "",
                "to_name": parties.get("to_name") or "",
                "to_address": parties.get("to_address") or "",
            })

        return {
            "piece_count": parsed["piece_count"],
            "mailpiece_count": parsed["mailpiece_count"],
            "package_count": parsed["package_count"],
            "gif_filename": gif_filename,
            "preview_images": preview_images,
            "tracking_numbers": parsed["tracking_numbers"],
            "section_counts": parsed["section_counts"],
            "date": parsed["date"],
            "letters": letters,
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


async def fetch_informed_delivery_history(
    mail_config: dict[str, Any],
    days: int = 30,
) -> list[dict[str, Any]]:
    """
    Fetch Informed Delivery digests for the last N days and build history days.

    Returns one entry per calendar day. When a day has two digests, the one
    with more mail-piece images is kept.
    """
    days = max(1, min(int(days or 30), 30))
    since = (_local_today() - timedelta(days=days - 1)).strftime("%d-%b-%Y")
    logger.info(
        "Fetching Informed Delivery history (%s days) for %s since %s",
        days,
        mail_config.get("imap_user"),
        since,
    )

    imap: imaplib.IMAP4_SSL | None = None
    ignore_sender = _should_ignore_sender(mail_config)
    try:
        imap, _selected = _connect_imap(mail_config)
        email_ids = _imap_search(imap, since, ignore_sender=ignore_sender)
        if not email_ids:
            return []

        by_date: dict[str, dict[str, Any]] = {}

        for email_id in email_ids:
            msg = _fetch_message(imap, email_id)
            if not msg or not _is_digest_message(msg):
                continue
            parsed = _process_informed_delivery_message(msg)
            day = parsed["date"]
            # Keep the digest that actually has the letter scans when a
            # later notice for the same mail day has none.
            existing = by_date.get(day)
            if existing is None or len(parsed["images"]) >= len(existing["images"]):
                by_date[day] = parsed

        history_days: list[dict[str, Any]] = []
        recipient = _known_recipient(mail_config)
        for day in sorted(by_date.keys()):
            parsed = by_date[day]
            letters = await _save_history_letter_images(
                mail_config.get("id", "mail"),
                day,
                parsed["images"],
                known_recipient=recipient,
            )
            history_days.append({
                "date": day,
                "mailpiece_count": parsed["mailpiece_count"],
                "package_count": parsed["package_count"],
                "piece_count": parsed["piece_count"],
                "letters": letters,
                "preview_images": [letter["image"] for letter in letters if letter.get("image")],
                "tracking_numbers": parsed["tracking_numbers"],
                "account_id": mail_config.get("id"),
            })

        logger.info("Built %s history day(s) from Informed Delivery", len(history_days))
        return history_days

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

    real_images = [_image_bytes(img) for img in images]
    real_images = [img for img in real_images if img]
    if not real_images:
        return [], None

    preview_images: list[str] = []
    safe_key = re.sub(r"[^a-zA-Z0-9]", "", str(account_key))[:12] or "mail"
    batch = uuid.uuid4().hex[:8]

    for index, img_bytes in enumerate(real_images):
        try:
            preview_bytes = prepare_preview_bytes(img_bytes)
            filename = f"mail_{safe_key}_{batch}_{index}.jpg"
            output_path = MAIL_IMAGES_DIR / filename
            output_path.write_bytes(preview_bytes)
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
        target_size = PREVIEW_MAX_SIZE

        for img_bytes in images:
            if not img_bytes:
                # Placeholder - create gray rectangle
                img = Image.new("RGB", target_size, color=(200, 200, 200))
            else:
                try:
                    img = Image.open(BytesIO(img_bytes)).convert("RGB")
                    img = enhance_mail_preview(img)
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
