"""
Home Delivery - FastAPI main application.
Package tracking and USPS Informed Delivery for Home Assistant.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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
CODE_VERSION = "1.0.0"

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


# ============================================================================
# Pydantic Models
# ============================================================================


class PackageCreate(BaseModel):
    tracking_number: str
    recipient: str = ""
    destination: str = ""
    carrier: str | None = None


class PackageUpdate(BaseModel):
    recipient: str | None = None
    destination: str | None = None
    carrier: str | None = None


class ConfigUpdate(BaseModel):
    config: dict[str, Any]


class TTSTestRequest(BaseModel):
    message: str
    media_player: str | None = None


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
    # Mask sensitive fields
    if config.get("mail", {}).get("imap_password"):
        config["mail"]["imap_password"] = "********"
    return {"config": config}


@app.put("/api/config")
async def update_config(request: Request):
    """Update configuration (auto-save target from UI)."""
    try:
        body = await request.json()
        updates = body.get("config", body)

        # Don't overwrite password if masked value is sent back
        if updates.get("mail", {}).get("imap_password") == "********":
            current = await config_store.load()
            updates["mail"]["imap_password"] = current.get("mail", {}).get("imap_password", "")

        config = await config_store.update(updates)

        # Mask password in response
        if config.get("mail", {}).get("imap_password"):
            config["mail"]["imap_password"] = "********"

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


@app.post("/api/packages")
async def add_package(pkg: PackageCreate):
    """Add a new package to track."""
    from carrier_detect import detect_carrier, get_tracking_url

    # Auto-detect carrier if not provided
    carrier = pkg.carrier
    if not carrier:
        carrier = detect_carrier(pkg.tracking_number)

    if not carrier:
        raise HTTPException(
            status_code=400,
            detail="Could not detect carrier. Please specify carrier manually.",
        )

    package = {
        "id": str(uuid.uuid4()),
        "tracking_number": pkg.tracking_number.strip().upper(),
        "carrier": carrier,
        "recipient": pkg.recipient.strip(),
        "destination": pkg.destination.strip(),
        "tracking_url": get_tracking_url(carrier, pkg.tracking_number),
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
    config = await config_store.load()

    gif_url = None
    if mail_state.get("gif_filename"):
        gif_url = f"/api/mail/image/{mail_state['gif_filename']}"

    return {
        "enabled": mail_state.get("enabled", False),
        "configured": bool(config.get("mail", {}).get("imap_host")),
        "last_check": mail_state.get("last_check"),
        "piece_count": mail_state.get("piece_count", 0),
        "gif_url": gif_url,
    }


@app.post("/api/mail/refresh")
async def refresh_mail():
    """Force IMAP check for Informed Delivery."""
    config = await config_store.load()
    mail_config = config.get("mail", {})

    if not mail_config.get("enabled"):
        raise HTTPException(status_code=400, detail="Mail tracking not enabled")

    if not mail_config.get("imap_host"):
        raise HTTPException(status_code=400, detail="IMAP not configured")

    try:
        from mail.informed_delivery import check_informed_delivery
        result = await check_informed_delivery(mail_config)
        await config_store.update_mail_state(
            piece_count=result.get("piece_count", 0),
            gif_filename=result.get("gif_filename"),
        )
        return {
            "success": True,
            "piece_count": result.get("piece_count", 0),
            "gif_url": f"/api/mail/image/{result['gif_filename']}" if result.get("gif_filename") else None,
        }
    except Exception as e:
        logger.error(f"Mail check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
    mail = config.get("mail", {})

    # Calculate summary stats
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
            "enabled": mail.get("enabled", False),
            "piece_count": mail.get("piece_count", 0),
            "last_check": mail.get("last_check"),
            "gif_filename": mail.get("gif_filename"),
        },
    }


# ============================================================================
# Frontend Static Files
# ============================================================================

# Mount frontend at root (after API routes)
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


@app.get("/")
async def serve_index():
    """Serve the frontend index.html."""
    index_path = frontend_path / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse({"message": "Home Delivery API", "version": CODE_VERSION})
