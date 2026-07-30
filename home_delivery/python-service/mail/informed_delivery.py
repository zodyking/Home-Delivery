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
    folder = mail_config.get("folder", "INBOX")

    if not all([host, user, password]):
        raise ValueError("IMAP credentials not configured")

    logger.info(f"Checking Informed Delivery via IMAP: {host}")

    # Connect to IMAP
    try:
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(user, password)
        imap.select(folder)
    except Exception as e:
        logger.error(f"IMAP connection failed: {e}")
        raise

    try:
        # Search for today's Informed Delivery email
        search_query = _build_search_query(USPS_SENDERS, DIGEST_SUBJECT, _get_today_imap_date())
        logger.debug(f"IMAP search: {search_query}")

        # Try UTF-8 charset first
        try:
            status, data = imap.search("UTF-8", search_query)
        except Exception:
            status, data = imap.search(None, search_query)

        if status != "OK" or not data[0]:
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
        try:
            imap.close()
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
