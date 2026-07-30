"""
Adaptive polling scheduler for package tracking.
Polls hourly by default, every 5 minutes when out for delivery.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from config_store import config_store
from scrapers import scrape_package
from tts_triggers import trigger_package_tts, trigger_mail_tts

logger = logging.getLogger(__name__)

# Global scheduler task reference
_scheduler_task: asyncio.Task | None = None
_mail_scheduler_task: asyncio.Task | None = None


def _compute_fingerprint(events: list[dict]) -> str:
    """Compute fingerprint from events for change detection."""
    if not events:
        return ""
    latest = events[0]
    data = f"{latest.get('date', '')}{latest.get('description', '')}"
    return hashlib.md5(data.encode()).hexdigest()[:12]


async def _poll_package(package: dict[str, Any]) -> dict[str, Any] | None:
    """
    Poll a single package and detect changes.

    Returns:
        Updated package dict if changes detected, None otherwise.
    """
    pkg_id = package.get("id")
    tracking = package.get("tracking_number")
    logger.info(f"Polling package: {tracking}")

    try:
        result = await scrape_package(package)
        if not result:
            return None

        # Handle carrier change from re-probe
        if result.get("carrier_changed"):
            new_carrier = result.pop("new_carrier", None)
            new_tracking_url = result.pop("new_tracking_url", None)
            result.pop("carrier_changed", None)

            if new_carrier:
                logger.info(f"Updating carrier for {tracking}: {package.get('carrier')} -> {new_carrier}")
                result["carrier"] = new_carrier
            if new_tracking_url:
                result["tracking_url"] = new_tracking_url

        # Clear error on successful scrape (even if no new events)
        if not result.get("error"):
            result["error"] = None

        # Check for changes
        old_fingerprint = package.get("last_event_fingerprint")
        new_fingerprint = result.get("last_event_fingerprint")
        status_changed = old_fingerprint and new_fingerprint and old_fingerprint != new_fingerprint

        # Check for status transitions
        was_ofd = package.get("out_for_delivery", False)
        is_ofd = result.get("out_for_delivery", False)
        was_delivered = package.get("delivered", False)
        is_delivered = result.get("delivered", False)

        newly_ofd = is_ofd and not was_ofd
        newly_delivered = is_delivered and not was_delivered

        # Determine next poll interval
        config = await config_store.load()
        polling = config.get("polling", {})
        default_interval = polling.get("default_interval_seconds", 3600)
        ofd_interval = polling.get("out_for_delivery_interval_seconds", 300)

        if is_delivered:
            # Delivered packages poll less frequently
            interval = default_interval * 2
        elif is_ofd:
            # Out for delivery gets fast polling
            interval = ofd_interval
        else:
            interval = default_interval

        now = datetime.now(timezone.utc)
        result["poll_interval_seconds"] = interval
        result["next_poll_at"] = (now + timedelta(seconds=interval)).isoformat()

        # Update package in storage
        await config_store.update_package(pkg_id, result)

        # Trigger TTS if status changed
        if status_changed or newly_ofd or newly_delivered:
            merged = {**package, **result}
            await trigger_package_tts(
                merged,
                status_changed=status_changed,
                newly_ofd=newly_ofd,
                newly_delivered=newly_delivered,
            )

        return result

    except Exception as e:
        logger.error(f"Error polling {tracking}: {e}")
        await config_store.update_package(pkg_id, {
            "error": str(e),
            "last_polled": datetime.now(timezone.utc).isoformat(),
        })
        return None


async def _poll_all_packages() -> None:
    """Poll all packages that are due for refresh."""
    now = datetime.now(timezone.utc)
    packages = await config_store.get_packages()

    for pkg in packages:
        # Skip delivered packages older than 24 hours
        if pkg.get("delivered"):
            delivered_at = pkg.get("delivered_at")
            if delivered_at:
                try:
                    dt = datetime.fromisoformat(delivered_at.replace("Z", "+00:00"))
                    if (now - dt).total_seconds() > 86400:
                        continue
                except Exception:
                    pass

        # Check if due for poll
        next_poll = pkg.get("next_poll_at")
        if next_poll:
            try:
                next_dt = datetime.fromisoformat(next_poll.replace("Z", "+00:00"))
                if now < next_dt:
                    continue
            except Exception:
                pass

        await _poll_package(pkg)

        # Small delay between packages to avoid overwhelming scrapers
        await asyncio.sleep(2)


async def _poll_mail() -> None:
    """Check for new Informed Delivery mail across all enabled accounts."""
    config = await config_store.load()
    accounts = config.get("mail", {}).get("accounts", [])
    enabled = [a for a in accounts if a.get("enabled", True)]

    if not enabled:
        return

    try:
        from mail.informed_delivery import check_informed_delivery

        old_total = sum(int(a.get("piece_count") or 0) for a in enabled)
        new_total = 0

        for account in enabled:
            if not all([account.get("imap_host"), account.get("imap_user"), account.get("imap_password")]):
                continue
            try:
                result = await check_informed_delivery(account)
                piece_count = result.get("piece_count", 0)
                new_total += piece_count
                await config_store.update_mail_state(
                    account_id=account["id"],
                    piece_count=piece_count,
                    mailpiece_count=result.get("mailpiece_count", piece_count),
                    package_count=result.get("package_count", 0),
                    gif_filename=result.get("gif_filename"),
                    preview_images=result.get("preview_images") or [],
                    last_error=None,
                )
                try:
                    from mail.discover_packages import add_discovered_packages

                    await add_discovered_packages(
                        result.get("tracking_numbers") or [],
                        account=account,
                    )
                except Exception as discover_exc:
                    logger.warning(
                        "Failed to auto-add discovered packages for %s: %s",
                        account.get("label"),
                        discover_exc,
                    )
            except Exception as e:
                logger.error(f"Mail check failed for {account.get('label')}: {e}")
                await config_store.update_mail_state(
                    account_id=account["id"],
                    piece_count=account.get("piece_count", 0),
                    mailpiece_count=account.get("mailpiece_count", account.get("piece_count", 0)),
                    package_count=account.get("package_count", 0),
                    gif_filename=account.get("gif_filename"),
                    preview_images=account.get("preview_images") or [],
                    last_error=str(e),
                )

        if new_total > old_total:
            await trigger_mail_tts(new_total)

    except Exception as e:
        logger.error(f"Mail check failed: {e}")


async def _scheduler_loop() -> None:
    """Main scheduler loop - runs continuously."""
    logger.info("Package polling scheduler started")

    # Initial poll after startup delay
    await asyncio.sleep(30)

    while True:
        try:
            await _poll_all_packages()
        except Exception as e:
            logger.error(f"Scheduler error: {e}")

        # Check every minute for packages that need polling
        await asyncio.sleep(60)


async def _mail_scheduler_loop() -> None:
    """Mail polling scheduler - runs on a longer interval."""
    logger.info("Mail polling scheduler started")

    # Initial check after startup
    await asyncio.sleep(60)

    while True:
        try:
            await _poll_mail()
        except Exception as e:
            logger.error(f"Mail scheduler error: {e}")

        # Check mail every 30 minutes during daytime
        now = datetime.now()
        hour = now.hour

        if 6 <= hour < 18:
            # Daytime: check every 30 minutes
            interval = 1800
        else:
            # Nighttime: check every 2 hours
            interval = 7200

        await asyncio.sleep(interval)


def start_scheduler() -> None:
    """Start the background polling scheduler."""
    global _scheduler_task, _mail_scheduler_task

    loop = asyncio.get_event_loop()

    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = loop.create_task(_scheduler_loop())

    if _mail_scheduler_task is None or _mail_scheduler_task.done():
        _mail_scheduler_task = loop.create_task(_mail_scheduler_loop())


def stop_scheduler() -> None:
    """Stop the background polling scheduler."""
    global _scheduler_task, _mail_scheduler_task

    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None

    if _mail_scheduler_task and not _mail_scheduler_task.done():
        _mail_scheduler_task.cancel()
        _mail_scheduler_task = None
