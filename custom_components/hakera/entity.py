"""Base entity helpers for Makera Z1."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import MakeraZ1Coordinator


class MakeraZ1Entity(CoordinatorEntity[MakeraZ1Coordinator]):
    """Base class for Makera Z1 entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MakeraZ1Coordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_identifier}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return dynamic device info."""
        identity = self.coordinator.data.identity if self.coordinator.data else None
        model = identity.model if identity and identity.model else MODEL
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_identifier)},
            manufacturer=MANUFACTURER,
            model=model,
            name=self.coordinator.config_entry.title,
            serial_number=identity.serial if identity else None,
            sw_version=identity.firmware_version if identity else None,
            configuration_url=f"http://{self.coordinator.client.host}",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return no common extra attributes."""
        return None
