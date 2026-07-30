"""Data coordinator for Home Delivery integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class HomeDeliveryDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Home Delivery data from the addon API."""

    def __init__(self, hass: HomeAssistant, addon_url: str) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.addon_url = addon_url.rstrip("/")
        self.hass = hass

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from addon API."""
        session = async_get_clientsession(self.hass)

        try:
            async with session.get(
                f"{self.addon_url}/api/state",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise UpdateFailed(f"API returned {response.status}")
                data = await response.json()
                return data

        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error communicating with addon: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

    @property
    def packages(self) -> list[dict[str, Any]]:
        """Get packages from coordinator data."""
        if self.data:
            return self.data.get("packages", [])
        return []

    @property
    def summary(self) -> dict[str, Any]:
        """Get summary from coordinator data."""
        if self.data:
            return self.data.get("summary", {})
        return {}

    @property
    def mail(self) -> dict[str, Any]:
        """Get mail state from coordinator data."""
        if self.data:
            return self.data.get("mail", {})
        return {}
