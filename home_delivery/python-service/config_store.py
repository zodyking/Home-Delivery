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
STORAGE_VERSION = 3

DEFAULT_CONFIG: dict[str, Any] = {
    "_version": STORAGE_VERSION,
    "appearance": {
        "mode": "dark",
        "overrides": {},
    },
    "mail": {
        "accounts": [],
        "history": {},
    },
    "media_players": [],
    # Per announcement type → entity_id → { volume, bypass } (home-weather parity)
    "announcement_players": {},
    "message_prefix": "Message from Home Delivery",
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


def _migrate_mail_config(mail: dict[str, Any]) -> dict[str, Any]:
    """Migrate legacy flat mail config to accounts list."""
    if not isinstance(mail, dict):
        return {"accounts": []}

    if mail.get("accounts"):
        return mail

    host = mail.get("imap_host", "")
    user = mail.get("imap_user", "")
    if not host and not user:
        return {"accounts": []}

    import uuid as _uuid

    account = {
        "id": str(_uuid.uuid4()),
        "label": mail.get("label") or "Home",
        "enabled": mail.get("enabled", True),
        "imap_host": host,
        "imap_port": mail.get("imap_port", 993),
        "imap_user": user,
        "imap_password": mail.get("imap_password", ""),
        "folder": mail.get("folder", "INBOX"),
        "last_check": mail.get("last_check"),
        "piece_count": mail.get("piece_count", 0),
        "gif_filename": mail.get("gif_filename"),
        "last_error": None,
    }
    return {"accounts": [account]}


def _aggregate_mail_state(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate mail state across all enabled accounts."""
    enabled_accounts = [a for a in accounts if a.get("enabled", True)]
    active = enabled_accounts if enabled_accounts else accounts

    piece_count = sum(int(a.get("piece_count") or 0) for a in active)
    mailpiece_count = sum(int(a.get("mailpiece_count") or 0) for a in active)
    package_count = sum(int(a.get("package_count") or 0) for a in active)
    last_checks = [a.get("last_check") for a in active if a.get("last_check")]
    last_check = max(last_checks) if last_checks else None

    gif_filename = None
    for account in active:
        if account.get("gif_filename"):
            gif_filename = account["gif_filename"]
            break

    configured = any(
        a.get("imap_host") and a.get("imap_user") and a.get("imap_password")
        for a in accounts
    )
    enabled = any(a.get("enabled", True) for a in accounts if a.get("imap_host"))

    return {
        "configured": configured,
        "enabled": enabled,
        "piece_count": piece_count,
        "mailpiece_count": mailpiece_count,
        "package_count": package_count,
        "last_check": last_check,
        "gif_filename": gif_filename,
        "accounts": accounts,
    }


def _migrate_config(config: dict) -> dict:
    """Migrate config from older versions if needed."""
    version = config.get("_version", 0)
    if version < STORAGE_VERSION:
        config = _deep_merge(DEFAULT_CONFIG, config)
        if "mail" in config:
            config["mail"] = _migrate_mail_config(config.get("mail", {}))
        config["_version"] = STORAGE_VERSION
    elif "mail" in config and not config["mail"].get("accounts"):
        config["mail"] = _migrate_mail_config(config.get("mail", {}))
    if config.get("message_prefix") in ("Home Delivery update", "Home Delivery update."):
        config["message_prefix"] = DEFAULT_CONFIG["message_prefix"]
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
            if "mail" in self._config:
                self._config["mail"] = _migrate_mail_config(self._config["mail"])
                if self._config["mail"].get("accounts") is not None:
                    self._config["mail"] = {"accounts": self._config["mail"]["accounts"]}
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
        """Get aggregated mail tracking state."""
        config = await self.load()
        mail = config.get("mail", {})
        accounts = mail.get("accounts", [])
        return _aggregate_mail_state(accounts)

    async def get_mail_accounts(self) -> list[dict[str, Any]]:
        """Get all mail accounts."""
        config = await self.load()
        return copy.deepcopy(config.get("mail", {}).get("accounts", []))

    async def update_mail_account(self, account_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update a mail account by ID."""
        async with self._lock:
            if self._config is None:
                await self.load()

            accounts = self._config.get("mail", {}).get("accounts", [])
            for i, account in enumerate(accounts):
                if account.get("id") == account_id:
                    accounts[i] = _deep_merge(account, updates)
                    self._config["mail"]["accounts"] = accounts
                    await self._save_locked()
                    return copy.deepcopy(accounts[i])
            return None

    async def update_mail_state(
        self,
        account_id: str,
        piece_count: int,
        gif_filename: str | None = None,
        preview_images: list[str] | None = None,
        mailpiece_count: int | None = None,
        package_count: int | None = None,
        last_error: str | None = None,
    ) -> None:
        """Update mail state for a specific account after IMAP check."""
        updates: dict[str, Any] = {
            "piece_count": piece_count,
            "mailpiece_count": mailpiece_count if mailpiece_count is not None else piece_count,
            "package_count": package_count or 0,
            "gif_filename": gif_filename,
            "preview_images": preview_images or [],
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_error": last_error,
        }
        await self.update_mail_account(account_id, updates)

    async def get_mail_history(self) -> dict[str, Any]:
        """Return mail history keyed by YYYY-MM-DD."""
        config = await self.load()
        history = config.get("mail", {}).get("history") or {}
        return copy.deepcopy(history if isinstance(history, dict) else {})

    async def upsert_mail_history_day(self, day: dict[str, Any]) -> dict[str, Any]:
        """Insert or replace a single history day entry (account-scoped merge)."""
        date_key = str(day.get("date") or "").strip()
        if not date_key:
            raise ValueError("History day requires a date")

        # Ensure config is loaded before taking the write lock (load() also locks).
        if self._config is None:
            await self.load()

        async with self._lock:
            mail = self._config.setdefault("mail", {"accounts": [], "history": {}})
            history = mail.setdefault("history", {})
            if not isinstance(history, dict):
                history = {}
                mail["history"] = history

            existing = history.get(date_key) or {}
            account_id = day.get("account_id")

            # Replace this account's letters on re-sync so filenames/OCR don't pile up.
            prior_letters = list(existing.get("letters") or [])
            if account_id:
                merged_letters = [
                    letter for letter in prior_letters
                    if letter.get("account_id") != account_id
                ]
            else:
                merged_letters = list(prior_letters)

            seen_images = {letter.get("image") for letter in merged_letters if letter.get("image")}
            for letter in day.get("letters") or []:
                image = letter.get("image")
                if image and image in seen_images:
                    continue
                stamped = dict(letter)
                if account_id:
                    stamped["account_id"] = account_id
                merged_letters.append(stamped)
                if image:
                    seen_images.add(image)

            # Per-account counts avoid double-counting when the same account syncs again.
            by_account = dict(existing.get("by_account") or {})
            day_counts = {
                "mailpiece_count": int(day.get("mailpiece_count") or 0),
                "package_count": int(day.get("package_count") or 0),
                "piece_count": int(day.get("piece_count") or (
                    int(day.get("mailpiece_count") or 0) + int(day.get("package_count") or 0)
                )),
            }
            if account_id:
                by_account[str(account_id)] = day_counts
                account_ids = sorted(by_account.keys())
                mailpiece_count = sum(int(v.get("mailpiece_count") or 0) for v in by_account.values())
                package_count = sum(int(v.get("package_count") or 0) for v in by_account.values())
                piece_count = mailpiece_count + package_count
            else:
                account_ids = list(existing.get("account_ids") or [])
                mailpiece_count = day_counts["mailpiece_count"]
                package_count = day_counts["package_count"]
                piece_count = day_counts["piece_count"]

            tracking = list(existing.get("tracking_numbers") or [])
            for tn in day.get("tracking_numbers") or []:
                if tn and tn not in tracking:
                    tracking.append(tn)

            entry = {
                "date": date_key,
                "mailpiece_count": mailpiece_count,
                "package_count": package_count,
                "piece_count": piece_count,
                "letters": merged_letters,
                "preview_images": [
                    letter.get("image") for letter in merged_letters if letter.get("image")
                ],
                "tracking_numbers": tracking,
                "account_ids": account_ids,
                "by_account": by_account,
            }

            history[date_key] = entry
            # Keep at most ~45 days of history
            for old_key in sorted(history.keys())[:-45]:
                history.pop(old_key, None)

            await self._save_locked()
            return copy.deepcopy(entry)

    async def replace_mail_history(self, days: list[dict[str, Any]]) -> dict[str, Any]:
        """Replace/merge a batch of history days (used by 30-day backfill)."""
        for day in days:
            await self.upsert_mail_history_day(day)
        return await self.get_mail_history()


# Global singleton
config_store = ConfigStore()
