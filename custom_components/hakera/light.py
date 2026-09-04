"""Light platform for Makera Z1."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import LightEntity
from homeassistant.components.light.const import ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MakeraZ1ConfigEntry
from .const import DOMAIN
from .entity import MakeraZ1Entity
from .z1 import MakeraZ1Error, diagnostic_switch_is_active

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakeraZ1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Makera Z1 work light."""
    async_add_entities([MakeraZ1WorkLight(entry.runtime_data.coordinator)])


class MakeraZ1WorkLight(MakeraZ1Entity, LightEntity):
    """Feedback-backed work-light control."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_icon = "mdi:lightbulb"
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_translation_key = "work_light"

    def __init__(self, coordinator) -> None:
        """Initialize the work light."""
        super().__init__(coordinator, "work_light_control")

    @property
    def is_on(self) -> bool | None:
        """Return the work-light state reported by the controller."""
        if not self.coordinator.data:
            return None
        return diagnostic_switch_is_active(
            self.coordinator.data.diagnostic_fields.get("workLight")
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the work light."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the work light."""
        await self._async_set_state(False)

    async def _async_set_state(self, enabled: bool) -> None:
        """Set and confirm the work-light state."""
        try:
            diagnostic = await self.coordinator.client.async_set_work_light(enabled)
        except (MakeraZ1Error, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="work_light_command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self.coordinator.async_apply_diagnostic(diagnostic)
