"""Camera platform for Makera Z1."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MakeraZ1ConfigEntry
from .entity import MakeraZ1Entity
from .z1 import MakeraZ1CameraBusy, MakeraZ1ConnectionError, MakeraZ1ResponseError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakeraZ1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Makera Z1 camera."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities([MakeraZ1Camera(coordinator)])


class MakeraZ1Camera(MakeraZ1Entity, Camera):
    """On-demand Makera Z1 camera."""

    _attr_content_type = "image/jpeg"
    _attr_frame_interval = 1.0
    _attr_supported_features = 0
    _attr_translation_key = "live_view"

    def __init__(self, coordinator) -> None:
        """Initialize the camera."""
        super().__init__(coordinator, "camera")

    @property
    def is_on(self) -> bool:
        """Return whether the camera should be considered on."""
        return self.coordinator.last_update_success

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return one on-demand JPEG image."""
        try:
            return await self.coordinator.client.async_get_camera_image()
        except MakeraZ1CameraBusy as err:
            _LOGGER.info("Makera Z1 camera stream is busy: %s", err)
        except (MakeraZ1ConnectionError, MakeraZ1ResponseError) as err:
            _LOGGER.debug("Unable to fetch Makera Z1 camera image: %s", err)
        return None
