"""
USPS Informed Delivery IMAP scanner.
Ported from Mail-And-Packages integration.
"""
from __future__ import annotations

import email
import imaplib
import logging
import os
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

# Subject for daily digest
DIGEST_SUBJECT = "Your Daily Digest"

# Images to filter out (not actual mail pieces)
FILTER_PATTERNS = [
    "mailerProvidedImage",
    "ra_0",
    "Mail Attachment.txt",
    "logo",
    "header",
    "footer",
    "banner",
]

# Placeholder image reference in HTML when no scan available
NO_MAILPIECE_PATTERN = re.compile(r"\bimage-no-mailpieces?700\.jpg\b", re.IGNORECASE)


def _get_today_imap_date() -> str:
    """Get today's date in IMAP search format (DD-Mon-YYYY)."""
    return datetime.now().strftime("%d-%b-%Y")


def _build_search_query(senders: list[str], subject: str, since_date: str) -> str:
    """Build IMAP search query for Informed Delivery."""
    # OR together multiple senders
    if len(senders) == 1:
        from_clause = f'FROM "{senders[0]}"'
    else:
        # Build nested OR for multiple senders
        from_clause = f'FROM "{senders[0]}"'
        for sender in senders[1:]:
            from_clause = f'(OR {from_clause} FROM "{sender}")'

    return f'({from_clause} SUBJECT "{subject}" SINCE {since_date})'


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


def _extract_images_from_message(msg: Message) -> list[bytes]:
    """Extract mail piece images from email message."""
    images: list[bytes] = []

    for part in msg.walk():
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition", ""))

        # Skip non-attachment parts
        if "attachment" not in content_disposition and "inline" not in content_disposition:
            continue

        # Only process images
        if not content_type.startswith("image/"):
            continue

        filename = part.get_filename() or ""
        if not _is_valid_mail_image(filename):
            continue

        payload = part.get_payload(decode=True)
        if payload:
            images.append(payload)

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
        search_query = _build_search_query(USPS_SENDERS, DIGEST_SUBJECT, _get_today_imap_date())
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
            return {"piece_count": 0, "gif_filename": None}

        # Get the latest matching email
        email_ids = data[0].split()
        latest_id = email_ids[-1]

        status, msg_data = imap.fetch(latest_id, "(RFC822)")
        if status != "OK" or not msg_data[0]:
            logger.warning("Failed to fetch email")
            return {"piece_count": 0, "gif_filename": None}

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        # Extract mail piece images
        images = _extract_images_from_message(msg)

        # Check for placeholder (mail expected but no image)
        if _has_missing_mailpiece_placeholder(msg):
            # Add a placeholder count
            images.append(b"")  # Empty placeholder

        piece_count = len(images)
        logger.info(f"Found {piece_count} mail pieces")

        # Generate GIF if we have images
        gif_filename = None
        if images and any(img for img in images):
            gif_filename = await _generate_mail_gif(images)

        return {"piece_count": piece_count, "gif_filename": gif_filename}

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

        # Write animated GIF (2 second per frame)
        imageio.mimwrite(str(output_path), np_frames, duration=2000, loop=0)

        logger.info(f"Generated mail GIF: {filename}")
        return filename

    except ImportError as e:
        logger.error(f"Missing dependency for GIF generation: {e}")
        return None
    except Exception as e:
        logger.error(f"GIF generation failed: {e}")
        return None
