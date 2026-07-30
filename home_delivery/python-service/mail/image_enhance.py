"""
Shared enhancement for Informed Delivery letter scans (OCR + UI previews).
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

# Landscape envelope aspect used across carousel, GIF, and history.
PREVIEW_MAX_SIZE = (960, 432)
PREVIEW_JPEG_QUALITY = 92


def enhance_mail_preview(img: Image.Image) -> Image.Image:
    """
    Improve washed-out grayscale scans for display and downstream OCR.

    Applies mild contrast, brightness, and sharpening while keeping a white background.
    """
    rgb = img.convert("RGB")
    gray = ImageOps.grayscale(rgb)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(1.35)
    gray = ImageEnhance.Brightness(gray).enhance(1.08)
    rgb = Image.merge("RGB", (gray, gray, gray))
    rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.2, percent=130, threshold=2))
    return rgb


def prepare_preview_bytes(img_bytes: bytes) -> bytes:
    """Return enhanced JPEG bytes sized for carousel/history storage."""
    img = Image.open(BytesIO(img_bytes)).convert("RGB")
    img = enhance_mail_preview(img)
    img.thumbnail(PREVIEW_MAX_SIZE, Image.Resampling.LANCZOS)
    out = BytesIO()
    img.save(out, format="JPEG", quality=PREVIEW_JPEG_QUALITY, optimize=True)
    return out.getvalue()
