"""Select platform for Makera Z1."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MakeraZ1ConfigEntry
from .const import DOMAIN
from .entity import MakeraZ1Entity
from .z1 import (
    CAMERA_RESOLUTION_OPTIONS,
    CAMERA_RESOLUTIONS,
    MakeraZ1Error,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakeraZ1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the camera-resolution selector."""
    async_add_entities([MakeraZ1CameraResolution(entry.runtime_data.coordinator)])


class MakeraZ1CameraResolution(MakeraZ1Entity, SelectEntity):
    """Select the live Z1 camera frame size."""

    _attr_icon = "mdi:video-high-definition"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(CAMERA_RESOLUTION_OPTIONS)
    _attr_translation_key = "camera_resolution"

    def __init__(self, coordinator) -> None:
        """Initialize the selector."""
        super().__init__(coordinator, "camera_resolution")

    @property
    def current_option(self) -> str | None:
        """Return the observed or most recently confirmed frame size."""
        return self.coordinator.client.camera_resolution_option

    async def async_select_option(self, option: str) -> None:
        """Select and verify a firmware-supported frame size."""
        resolution = next(
            (item for item in CAMERA_RESOLUTIONS if item.option == option), None
        )
        if resolution is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_camera_resolution",
                translation_placeholders={"option": option},
            )

        try:
            await self.coordinator.client.async_set_camera_resolution(resolution.value)
        except (MakeraZ1Error, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="camera_resolution_command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self.async_write_ha_state()
