"""Sensor platform for Home Delivery integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HomeDeliveryDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Home Delivery sensors from a config entry."""
    coordinator: HomeDeliveryDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        HomeDeliveryActivePackagesSensor(coordinator, entry),
        HomeDeliveryOutForDeliverySensor(coordinator, entry),
        HomeDeliveryDeliveredTodaySensor(coordinator, entry),
        HomeDeliveryMailSensor(coordinator, entry),
    ]

    # Add per-package sensors
    if coordinator.data:
        for package in coordinator.packages:
            entities.append(HomeDeliveryPackageSensor(coordinator, entry, package))

    async_add_entities(entities)

    # Listen for new packages
    @callback
    def _async_update_packages() -> None:
        """Update package sensors when data changes."""
        existing_ids = {
            e.unique_id
            for e in hass.data.get("entity_registry", {}).get(DOMAIN, {}).values()
            if hasattr(e, "unique_id")
        }

        new_entities = []
        for package in coordinator.packages:
            pkg_id = package.get("id")
            unique_id = f"{entry.entry_id}_package_{pkg_id}"
            if unique_id not in existing_ids:
                new_entities.append(
                    HomeDeliveryPackageSensor(coordinator, entry, package)
                )

        if new_entities:
            async_add_entities(new_entities)

    coordinator.async_add_listener(_async_update_packages)


class HomeDeliveryBaseSensor(CoordinatorEntity[HomeDeliveryDataUpdateCoordinator], SensorEntity):
    """Base class for Home Delivery sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HomeDeliveryDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Home Delivery",
            manufacturer="Home Delivery",
            model="Package Tracker",
        )


class HomeDeliveryActivePackagesSensor(HomeDeliveryBaseSensor):
    """Sensor for active packages count."""

    _attr_name = "Active Packages"
    _attr_icon = "mdi:package-variant"

    def __init__(
        self,
        coordinator: HomeDeliveryDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_active_packages"

    @property
    def native_value(self) -> int:
        """Return the number of active packages."""
        return self.coordinator.summary.get("active", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "total": self.coordinator.summary.get("total", 0),
            "out_for_delivery": self.coordinator.summary.get("out_for_delivery", 0),
            "delivered_today": self.coordinator.summary.get("delivered_today", 0),
        }


class HomeDeliveryOutForDeliverySensor(HomeDeliveryBaseSensor):
    """Sensor for out for delivery packages count."""

    _attr_name = "Out for Delivery"
    _attr_icon = "mdi:truck-delivery"

    def __init__(
        self,
        coordinator: HomeDeliveryDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_out_for_delivery"

    @property
    def native_value(self) -> int:
        """Return the number of packages out for delivery."""
        return self.coordinator.summary.get("out_for_delivery", 0)


class HomeDeliveryDeliveredTodaySensor(HomeDeliveryBaseSensor):
    """Sensor for packages delivered today."""

    _attr_name = "Delivered Today"
    _attr_icon = "mdi:package-variant-closed-check"

    def __init__(
        self,
        coordinator: HomeDeliveryDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_delivered_today"

    @property
    def native_value(self) -> int:
        """Return the number of packages delivered today."""
        return self.coordinator.summary.get("delivered_today", 0)


class HomeDeliveryMailSensor(HomeDeliveryBaseSensor):
    """Sensor for USPS mail pieces."""

    _attr_name = "USPS Mail"
    _attr_icon = "mdi:mailbox"

    def __init__(
        self,
        coordinator: HomeDeliveryDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_usps_mail"

    @property
    def native_value(self) -> int:
        """Return the number of mail pieces."""
        return self.coordinator.mail.get("piece_count", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        mail = self.coordinator.mail
        return {
            "enabled": mail.get("enabled", False),
            "last_check": mail.get("last_check"),
            "gif_filename": mail.get("gif_filename"),
        }


class HomeDeliveryPackageSensor(HomeDeliveryBaseSensor):
    """Sensor for individual package."""

    def __init__(
        self,
        coordinator: HomeDeliveryDataUpdateCoordinator,
        entry: ConfigEntry,
        package: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._package_id = package.get("id")
        self._tracking_number = package.get("tracking_number", "")
        self._attr_unique_id = f"{entry.entry_id}_package_{self._package_id}"
        self._attr_name = f"Package {self._tracking_number[-6:]}"

    @property
    def _package(self) -> dict[str, Any] | None:
        """Get current package data."""
        for pkg in self.coordinator.packages:
            if pkg.get("id") == self._package_id:
                return pkg
        return None

    @property
    def native_value(self) -> str:
        """Return the package status."""
        pkg = self._package
        if pkg:
            return pkg.get("status", "Unknown")
        return "Unknown"

    @property
    def icon(self) -> str:
        """Return the icon based on status."""
        pkg = self._package
        if not pkg:
            return "mdi:package-variant"
        if pkg.get("delivered"):
            return "mdi:package-variant-closed-check"
        if pkg.get("out_for_delivery"):
            return "mdi:truck-delivery"
        return "mdi:package-variant"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        pkg = self._package
        if not pkg:
            return {}
        return {
            "tracking_number": pkg.get("tracking_number"),
            "carrier": pkg.get("carrier"),
            "recipient": pkg.get("recipient"),
            "destination": pkg.get("destination"),
            "status_detail": pkg.get("status_detail"),
            "out_for_delivery": pkg.get("out_for_delivery"),
            "delivered": pkg.get("delivered"),
            "last_polled": pkg.get("last_polled"),
            "tracking_url": pkg.get("tracking_url"),
            "events": pkg.get("events", [])[:5],  # Last 5 events
        }
