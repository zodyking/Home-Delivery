"""
Home Delivery - FastAPI main application.
Package tracking and USPS Informed Delivery for Home Assistant.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config_store import config_store, DEFAULT_CONFIG
from data_config import DATA_DIR, WWW_DIR, MAIL_IMAGES_DIR

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Code version for deployment verification
CODE_VERSION = "0.0.1"

app = FastAPI(title="Home Delivery API")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def utc_now() -> datetime:
    """Get current UTC time."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Get current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def _mask_mail_passwords(config: dict[str, Any]) -> dict[str, Any]:
    """Mask IMAP passwords in mail accounts for API responses."""
    mail = config.get("mail")
    if not mail:
        return config
    accounts = mail.get("accounts", [])
    for account in accounts:
        if account.get("imap_password"):
            account["imap_password"] = "********"
    return config


def _restore_mail_passwords(updates: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Restore masked passwords when saving config."""
    new_accounts = updates.get("mail", {}).get("accounts")
    if not new_accounts:
        return updates

    current_accounts = {
        a.get("id"): a for a in current.get("mail", {}).get("accounts", [])
    }
    for account in new_accounts:
        if account.get("imap_password") == "********":
            existing = current_accounts.get(account.get("id"), {})
            account["imap_password"] = existing.get("imap_password", "")
    return updates


# ============================================================================
# Pydantic Models
# ============================================================================


class PackageCreate(BaseModel):
    tracking_number: str
    recipient: str = ""
    destination: str = ""
    carrier: str | None = None
    destination_account_id: str | None = None


class ProbeCarrierRequest(BaseModel):
    tracking_number: str


class PackageUpdate(BaseModel):
    recipient: str | None = None
    destination: str | None = None
    carrier: str | None = None


class ConfigUpdate(BaseModel):
    config: dict[str, Any]


class TTSTestRequest(BaseModel):
    message: str
    media_player: str | None = None


class MailImapTestRequest(BaseModel):
    imap_host: str
    imap_port: int = 993
    imap_user: str
    imap_password: str | None = None
    account_id: str | None = None


# ============================================================================
# Startup / Shutdown
# ============================================================================


@app.on_event("startup")
async def startup():
    """Initialize on startup."""
    logger.info("Home Delivery API starting...")
    await config_store.load()
    logger.info("Configuration loaded")

    # Start background polling scheduler
    from polling_scheduler import start_scheduler
    start_scheduler()
    logger.info("Polling scheduler started")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    logger.info("Home Delivery API shutting down...")

    # Stop polling scheduler
    from polling_scheduler import stop_scheduler
    stop_scheduler()

    # Close browser pool
    from scrapers.base import close_browser
    await close_browser()


# ============================================================================
# Version / Health Endpoints
# ============================================================================


@app.get("/api/version")
async def get_version():
    """Return API version."""
    return {"version": CODE_VERSION}


@app.get("/api/health")
async def get_health():
    """Health check endpoint."""
    return {"status": "ok", "version": CODE_VERSION}


# ============================================================================
# Configuration Endpoints
# ============================================================================


@app.get("/api/config")
async def get_config():
    """Get full configuration (passwords masked)."""
    config = await config_store.load()
    config = copy.deepcopy(config)
    _mask_mail_passwords(config)
    return {"config": config}


@app.put("/api/config")
async def update_config(request: Request):
    """Update configuration (auto-save target from UI)."""
    try:
        body = await request.json()
        updates = body.get("config", body)

        # Don't overwrite passwords if masked values are sent back
        current = await config_store.load()
        updates = _restore_mail_passwords(updates, current)

        config = await config_store.update(updates)

        config = copy.deepcopy(config)
        _mask_mail_passwords(config)

        return {"config": config, "success": True}
    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Package Endpoints
# ============================================================================


@app.get("/api/packages")
async def get_packages():
    """Get all tracked packages."""
    packages = await config_store.get_packages()
    return {"packages": packages}


@app.post("/api/packages/probe-carrier")
async def probe_carrier_endpoint(request: ProbeCarrierRequest):
    """Probe carriers to detect which one a tracking number belongs to."""
    from carrier_probe import probe_carrier_result

    result = await probe_carrier_result(request.tracking_number)

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@app.post("/api/packages")
async def add_package(pkg: PackageCreate):
    """Add a new package to track."""
    from carrier_detect import get_tracking_url, normalize_tracking_number
    from carrier_probe import probe_carrier

    # Auto-detect carrier if not provided using probe
    carrier = pkg.carrier
    if not carrier:
        carrier = await probe_carrier(pkg.tracking_number)

    if not carrier:
        raise HTTPException(
            status_code=400,
            detail="Could not find this tracking number at USPS, UPS, or FedEx. Please verify the number.",
        )

    normalized = normalize_tracking_number(pkg.tracking_number)

    package = {
        "id": str(uuid.uuid4()),
        "tracking_number": normalized,
        "carrier": carrier,
        "recipient": pkg.recipient.strip(),
        "destination": pkg.destination.strip(),
        "destination_account_id": pkg.destination_account_id,
        "tracking_url": get_tracking_url(carrier, normalized),
        "status": "Pending",
        "status_detail": "",
        "events": [],
        "last_event_fingerprint": None,
        "out_for_delivery": False,
        "delivered": False,
        "created_at": utc_now_iso(),
        "last_polled": None,
        "next_poll_at": utc_now_iso(),
        "poll_interval_seconds": 3600,
        "error": None,
    }

    result = await config_store.add_package(package)
    return {"package": result, "success": True}


@app.get("/api/packages/{package_id}")
async def get_package(package_id: str):
    """Get a specific package by ID."""
    packages = await config_store.get_packages()
    for pkg in packages:
        if pkg.get("id") == package_id:
            return {"package": pkg}
    raise HTTPException(status_code=404, detail="Package not found")


@app.patch("/api/packages/{package_id}")
async def update_package(package_id: str, updates: PackageUpdate):
    """Update package metadata."""
    update_dict = {k: v for k, v in updates.model_dump().items() if v is not None}
    result = await config_store.update_package(package_id, update_dict)
    if result is None:
        raise HTTPException(status_code=404, detail="Package not found")
    return {"package": result, "success": True}


@app.delete("/api/packages/{package_id}")
async def delete_package(package_id: str):
    """Remove a package from tracking."""
    success = await config_store.delete_package(package_id)
    if not success:
        raise HTTPException(status_code=404, detail="Package not found")
    return {"success": True}


@app.post("/api/packages/{package_id}/refresh")
async def refresh_package(package_id: str):
    """Force immediate scrape for a package."""
    packages = await config_store.get_packages()
    package = None
    for pkg in packages:
        if pkg.get("id") == package_id:
            package = pkg
            break

    if not package:
        raise HTTPException(status_code=404, detail="Package not found")

    # Import scraper and run
    try:
        from scrapers import scrape_package
        result = await scrape_package(package)
        if result:
            await config_store.update_package(package_id, result)
            return {"package": {**package, **result}, "success": True}
        return {"package": package, "success": True, "message": "No updates"}
    except Exception as e:
        logger.error(f"Scrape failed for {package_id}: {e}")
        await config_store.update_package(package_id, {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Mail Endpoints
# ============================================================================


@app.get("/api/mail")
async def get_mail():
    """Get USPS Informed Delivery mail state."""
    mail_state = await config_store.get_mail_state()

    gif_url = None
    if mail_state.get("gif_filename"):
        gif_url = f"/api/mail/image/{mail_state['gif_filename']}"

    return {
        "enabled": mail_state.get("enabled", False),
        "configured": mail_state.get("configured", False),
        "last_check": mail_state.get("last_check"),
        "piece_count": mail_state.get("piece_count", 0),
        "gif_url": gif_url,
        "accounts": [
            {
                "id": a.get("id"),
                "label": a.get("label"),
                "enabled": a.get("enabled", True),
                "imap_user": a.get("imap_user"),
                "piece_count": a.get("piece_count", 0),
                "last_check": a.get("last_check"),
                "last_error": a.get("last_error"),
            }
            for a in mail_state.get("accounts", [])
        ],
    }


async def _resolve_imap_password(
    imap_password: str | None,
    account_id: str | None,
) -> str:
    """Resolve IMAP password from request or stored account."""
    if imap_password and imap_password != "********":
        return imap_password

    if account_id:
        config = await config_store.load()
        for account in config.get("mail", {}).get("accounts", []):
            if account.get("id") == account_id:
                stored = account.get("imap_password", "")
                if stored:
                    return stored
                break

    raise HTTPException(status_code=400, detail="Password is required")


@app.post("/api/mail/test-imap")
async def test_imap_connection(request: MailImapTestRequest):
    """Validate IMAP credentials and return available mailbox folders."""
    from mail.informed_delivery import list_imap_folders

    password = await _resolve_imap_password(request.imap_password, request.account_id)

    try:
        folders = list_imap_folders(
            host=request.imap_host.strip(),
            port=request.imap_port or 993,
            user=request.imap_user.strip(),
            password=password,
        )
    except Exception as e:
        logger.error(f"IMAP test failed for {request.imap_user}: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    default_folder = "INBOX" if "INBOX" in folders else folders[0]

    return {
        "success": True,
        "folders": folders,
        "default_folder": default_folder,
    }


async def _sync_mail_accounts(account_ids: list[str] | None = None) -> dict[str, Any]:
    """Sync one or all mail accounts via IMAP."""
    from mail.informed_delivery import check_informed_delivery

    config = await config_store.load()
    accounts = config.get("mail", {}).get("accounts", [])

    if not accounts:
        raise HTTPException(status_code=400, detail="No mail accounts configured")

    targets = accounts
    if account_ids:
        targets = [a for a in accounts if a.get("id") in account_ids]
        if not targets:
            raise HTTPException(status_code=404, detail="Mail account not found")

    results = []
    total_pieces = 0
    primary_gif = None

    for account in targets:
        account_id = account.get("id")
        label = account.get("label") or account.get("imap_user") or "Account"
        if not all([account.get("imap_host"), account.get("imap_user"), account.get("imap_password")]):
            results.append({
                "id": account_id,
                "label": label,
                "success": False,
                "error": "IMAP credentials incomplete",
            })
            continue

        try:
            result = await check_informed_delivery(account)
            piece_count = result.get("piece_count", 0)
            gif_filename = result.get("gif_filename")
            await config_store.update_mail_state(
                account_id=account_id,
                piece_count=piece_count,
                gif_filename=gif_filename,
                last_error=None,
            )
            total_pieces += piece_count
            if gif_filename and not primary_gif:
                primary_gif = gif_filename
            results.append({
                "id": account_id,
                "label": label,
                "success": True,
                "piece_count": piece_count,
                "gif_url": f"/api/mail/image/{gif_filename}" if gif_filename else None,
            })
        except Exception as e:
            logger.error(f"Mail sync failed for {label}: {e}")
            await config_store.update_mail_state(
                account_id=account_id,
                piece_count=account.get("piece_count", 0),
                gif_filename=account.get("gif_filename"),
                last_error=str(e),
            )
            results.append({
                "id": account_id,
                "label": label,
                "success": False,
                "error": str(e),
            })

    successes = [r for r in results if r.get("success")]
    if not successes and results:
        first_error = results[0].get("error", "Sync failed")
        raise HTTPException(status_code=500, detail=first_error)

    return {
        "success": True,
        "piece_count": total_pieces,
        "gif_url": f"/api/mail/image/{primary_gif}" if primary_gif else None,
        "results": results,
    }


@app.post("/api/mail/sync")
async def sync_mail(request: Request):
    """Sync mail inbox — validates IMAP credentials and fetches latest mail."""
    body = {}
    try:
        raw = await request.body()
        if raw:
            body = json.loads(raw)
    except Exception:
        body = {}

    account_id = body.get("account_id") if isinstance(body, dict) else None
    account_ids = [account_id] if account_id else None
    return await _sync_mail_accounts(account_ids)


@app.post("/api/mail/refresh")
async def refresh_mail():
    """Force IMAP check for all enabled mail accounts."""
    config = await config_store.load()
    accounts = config.get("mail", {}).get("accounts", [])
    enabled_ids = [a["id"] for a in accounts if a.get("enabled", True)]

    if not enabled_ids:
        raise HTTPException(status_code=400, detail="No enabled mail accounts")

    return await _sync_mail_accounts(enabled_ids)


@app.get("/api/mail/image/{filename}")
async def get_mail_image(filename: str):
    """Serve mail GIF image."""
    # Sanitize filename
    safe_filename = Path(filename).name
    image_path = MAIL_IMAGES_DIR / safe_filename

    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(image_path, media_type="image/gif")


# ============================================================================
# Home Assistant Entity Discovery
# ============================================================================


@app.get("/api/ha-entities")
async def get_ha_entities():
    """Get media players and TTS entities from Home Assistant."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return {"media_players": [], "tts_entities": [], "error": "Not running as addon"}

    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {token}"}

            # Get all states
            async with session.get(
                "http://supervisor/core/api/states",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return {"media_players": [], "tts_entities": [], "error": f"HA API error: {resp.status}"}

                states = await resp.json()

        media_players = []
        tts_entities = []

        for state in states:
            entity_id = state.get("entity_id", "")
            friendly_name = state.get("attributes", {}).get("friendly_name", entity_id)

            if entity_id.startswith("media_player."):
                media_players.append({
                    "entity_id": entity_id,
                    "friendly_name": friendly_name,
                    "state": state.get("state"),
                })
            elif entity_id.startswith("tts."):
                tts_entities.append({
                    "entity_id": entity_id,
                    "friendly_name": friendly_name,
                })

        return {
            "media_players": media_players,
            "tts_entities": tts_entities,
        }

    except Exception as e:
        logger.error(f"Failed to get HA entities: {e}")
        return {"media_players": [], "tts_entities": [], "error": str(e)}


# ============================================================================
# TTS Test Endpoint
# ============================================================================


@app.post("/api/test-tts")
async def test_tts(request: TTSTestRequest):
    """Send a test TTS announcement."""
    try:
        from tts_engine import send_tts
        config = await config_store.load()
        media_players = config.get("media_players", [])

        if not media_players:
            raise HTTPException(status_code=400, detail="No media players configured")

        # Use specified player or first configured
        target_player = request.media_player
        if not target_player:
            target_player = media_players[0].get("entity_id")

        player_config = None
        for mp in media_players:
            if mp.get("entity_id") == target_player:
                player_config = mp
                break

        if not player_config:
            player_config = {"entity_id": target_player, "volume": 0.7}

        await send_tts(
            message=request.message,
            media_player=player_config.get("entity_id"),
            volume=player_config.get("volume", 0.7),
            tts_entity=player_config.get("tts_entity_id", "tts.google_translate_say"),
        )

        return {"success": True}

    except Exception as e:
        logger.error(f"TTS test failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Integration State Endpoint (for HA coordinator polling)
# ============================================================================


@app.get("/api/state")
async def get_state():
    """Get full state for HA integration coordinator."""
    config = await config_store.load()
    packages = config.get("packages", [])
    mail_state = await config_store.get_mail_state()

    active_packages = [p for p in packages if not p.get("delivered")]
    out_for_delivery = [p for p in packages if p.get("out_for_delivery") and not p.get("delivered")]
    delivered_today = [
        p for p in packages
        if p.get("delivered") and p.get("delivered_at", "").startswith(utc_now().strftime("%Y-%m-%d"))
    ]

    return {
        "packages": packages,
        "summary": {
            "total": len(packages),
            "active": len(active_packages),
            "out_for_delivery": len(out_for_delivery),
            "delivered_today": len(delivered_today),
        },
        "mail": {
            "enabled": mail_state.get("enabled", False),
            "configured": mail_state.get("configured", False),
            "piece_count": mail_state.get("piece_count", 0),
            "last_check": mail_state.get("last_check"),
            "gif_filename": mail_state.get("gif_filename"),
        },
    }


# ============================================================================
# Frontend Static Files
# ============================================================================

frontend_path = Path(__file__).parent.parent / "frontend"


@app.get("/")
async def serve_index():
    """Serve index.html with a versioned panel script URL for cache busting."""
    index_path = frontend_path / "index.html"
    if not index_path.exists():
        return JSONResponse({"message": "Home Delivery API", "version": CODE_VERSION})

    html = index_path.read_text(encoding="utf-8")
    html = html.replace(
        "__PANEL_VERSION__",
        CODE_VERSION,
    )
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


# Mount remaining frontend assets (delivery-panel.js, etc.) after explicit routes.
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
