"""
Centralized data directory configuration.
Supports DATA_DIR env var for Home Assistant addon (maps to /config).
"""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Subdirectories
WWW_DIR = DATA_DIR / "www" / "home_delivery"
WWW_DIR.mkdir(parents=True, exist_ok=True)

MAIL_IMAGES_DIR = DATA_DIR / "mail_images"
MAIL_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
