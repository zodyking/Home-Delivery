"""
JSON-based configuration storage for Home Delivery.
Modeled on home-weather's storage pattern.
"""
from __future__ import annotations

import json
import logging
import copy
from pathlib import Path
from typing import Any
import asyncio
from datetime import datetime, timezone

from data_config import DATA_DIR

logger = logging.getLogger(__name__)

CONFIG_FILE = DATA_DIR / "home_delivery_config.json"
STORAGE_VERSION = 1

DEFAULT_CONFIG: dict[str, Any] = {
    "_version": STORAGE_VERSION,
    "appearance": {
        "mode": "dark",
        "overrides": {},
    },
    "mail": {
        "enabled": False,
        "imap_host": "",
        "imap_port": 993,
        "imap_user": "",
        "imap_password": "",
        "folder": "INBOX",
        "last_check": None,
        "piece_count": 0,
        "gif_filename": None,
    },
    "media_players": [],
    "announcement_players": {},
    "tts": {
        "enabled": False,
        "enable_status_change": True,
        "enable_out_for_delivery": True,
        "enable_delivered": True,
        "enable_mail_arrived": True,
        "start_time": "08:00",
        "end_time": "21:00",
        "days_of_week": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "use_ai_rewrite": False,
        "ai_task_entity": "",
        "ai_rewrite_prompt": "You are a helpful assistant. Rewrite this package delivery update in a natural, conversational way. Keep it concise.",
    },
    "polling": {
        "default_interval_seconds": 3600,
        "out_for_delivery_interval_seconds": 300,
    },
    "packages": [],
}


def _deep_merge(base: dict, updates: dict) -> dict:
    """Deep merge updates into base, preserving base keys not in updates."""
    result = copy.deepcopy(base)
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _migrate_config(config: dict) -> dict:
    """Migrate config from older versions if needed."""
    version = config.get("_version", 0)
    if version < STORAGE_VERSION:
        config = _deep_merge(DEFAULT_CONFIG, config)
        config["_version"] = STORAGE_VERSION
    return config


class ConfigStore:
    """Thread-safe JSON configuration store."""

    def __init__(self):
        self._config: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def load(self) -> dict[str, Any]:
        """Load config from disk, merging with defaults."""
        async with self._lock:
            if self._config is not None:
                return copy.deepcopy(self._config)

            if CONFIG_FILE.exists():
                try:
                    raw = CONFIG_FILE.read_text(encoding="utf-8")
                    loaded = json.loads(raw)
                    self._config = _migrate_config(loaded)
                except Exception as e:
                    logger.error(f"Failed to load config: {e}")
                    self._config = copy.deepcopy(DEFAULT_CONFIG)
            else:
                self._config = copy.deepcopy(DEFAULT_CONFIG)
                await self._save_locked()

            return copy.deepcopy(self._config)

    async def save(self, config: dict[str, Any]) -> None:
        """Save config to disk."""
        async with self._lock:
            self._config = copy.deepcopy(config)
            await self._save_locked()

    async def _save_locked(self) -> None:
        """Save without acquiring lock (caller must hold lock)."""
        try:
            CONFIG_FILE.write_text(
                json.dumps(self._config, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    async def update(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Merge updates into config and save."""
        async with self._lock:
            if self._config is None:
                await self.load()
            self._config = _deep_merge(self._config, updates)
            await self._save_locked()
            return copy.deepcopy(self._config)

    async def get_packages(self) -> list[dict[str, Any]]:
        """Get all packages."""
        config = await self.load()
        return config.get("packages", [])

    async def add_package(self, package: dict[str, Any]) -> dict[str, Any]:
        """Add a new package."""
        async with self._lock:
            if self._config is None:
                # Load without lock since we already hold it
                if CONFIG_FILE.exists():
                    raw = CONFIG_FILE.read_text(encoding="utf-8")
                    self._config = _migrate_config(json.loads(raw))
                else:
                    self._config = copy.deepcopy(DEFAULT_CONFIG)

            packages = self._config.get("packages", [])
            packages.append(package)
            self._config["packages"] = packages
            await self._save_locked()
            return copy.deepcopy(package)

    async def update_package(self, package_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update an existing package by ID."""
        async with self._lock:
            if self._config is None:
                if CONFIG_FILE.exists():
                    raw = CONFIG_FILE.read_text(encoding="utf-8")
                    self._config = _migrate_config(json.loads(raw))
                else:
                    self._config = copy.deepcopy(DEFAULT_CONFIG)

            packages = self._config.get("packages", [])
            for i, pkg in enumerate(packages):
                if pkg.get("id") == package_id:
                    packages[i] = _deep_merge(pkg, updates)
                    self._config["packages"] = packages
                    await self._save_locked()
                    return copy.deepcopy(packages[i])
            return None

    async def delete_package(self, package_id: str) -> bool:
        """Delete a package by ID."""
        async with self._lock:
            if self._config is None:
                if CONFIG_FILE.exists():
                    raw = CONFIG_FILE.read_text(encoding="utf-8")
                    self._config = _migrate_config(json.loads(raw))
                else:
                    self._config = copy.deepcopy(DEFAULT_CONFIG)

            packages = self._config.get("packages", [])
            original_len = len(packages)
            packages = [p for p in packages if p.get("id") != package_id]

            if len(packages) < original_len:
                self._config["packages"] = packages
                await self._save_locked()
                return True
            return False

    async def get_mail_state(self) -> dict[str, Any]:
        """Get mail tracking state."""
        config = await self.load()
        mail = config.get("mail", {})
        return {
            "enabled": mail.get("enabled", False),
            "last_check": mail.get("last_check"),
            "piece_count": mail.get("piece_count", 0),
            "gif_filename": mail.get("gif_filename"),
        }

    async def update_mail_state(
        self,
        piece_count: int,
        gif_filename: str | None = None,
    ) -> None:
        """Update mail state after IMAP check."""
        async with self._lock:
            if self._config is None:
                if CONFIG_FILE.exists():
                    raw = CONFIG_FILE.read_text(encoding="utf-8")
                    self._config = _migrate_config(json.loads(raw))
                else:
                    self._config = copy.deepcopy(DEFAULT_CONFIG)

            if "mail" not in self._config:
                self._config["mail"] = {}

            self._config["mail"]["piece_count"] = piece_count
            self._config["mail"]["gif_filename"] = gif_filename
            self._config["mail"]["last_check"] = datetime.now(timezone.utc).isoformat()
            await self._save_locked()


# Global singleton
config_store = ConfigStore()
