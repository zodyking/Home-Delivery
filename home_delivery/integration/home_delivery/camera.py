"""Camera platform for Home Delivery integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_ADDON_URL
from .coordinator import HomeDeliveryDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Home Delivery cameras from a config entry."""
    coordinator: HomeDeliveryDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    addon_url = entry.data.get(CONF_ADDON_URL, "http://local-home-delivery:8000")

    async_add_entities([
        HomeDeliveryMailCamera(coordinator, entry, addon_url),
    ])


class HomeDeliveryMailCamera(CoordinatorEntity[HomeDeliveryDataUpdateCoordinator], Camera):
    """Camera for USPS Informed Delivery mail preview."""

    _attr_has_entity_name = True
    _attr_name = "USPS Mail Preview"

    def __init__(
        self,
        coordinator: HomeDeliveryDataUpdateCoordinator,
        entry: ConfigEntry,
        addon_url: str,
    ) -> None:
        """Initialize the camera."""
        super().__init__(coordinator)
        Camera.__init__(self)
        self._entry = entry
        self._addon_url = addon_url.rstrip("/")
        self._attr_unique_id = f"{entry.entry_id}_mail_camera"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Home Delivery",
            manufacturer="Home Delivery",
            model="Package Tracker",
        )

    @property
    def is_streaming(self) -> bool:
        """Return True if entity is streaming."""
        return False

    @property
    def _gif_filename(self) -> str | None:
        """Get GIF filename from mail state."""
        return self.coordinator.mail.get("gif_filename")

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera."""
        gif_filename = self._gif_filename
        if not gif_filename:
            return None

        session = async_get_clientsession(self.hass)
        url = f"{self._addon_url}/api/mail/image/{gif_filename}"

        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    return await response.read()
                _LOGGER.warning("Failed to fetch mail image: %s", response.status)
        except aiohttp.ClientError as err:
            _LOGGER.warning("Error fetching mail image: %s", err)

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        mail = self.coordinator.mail
        return {
            "piece_count": mail.get("piece_count", 0),
            "last_check": mail.get("last_check"),
            "enabled": mail.get("enabled", False),
        }
