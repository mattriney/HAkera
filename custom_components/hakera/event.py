"""Event platform for Makera Z1 controller alerts."""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MakeraZ1ConfigEntry
from .coordinator import MakeraZ1Coordinator
from .entity import MakeraZ1Entity
from .z1 import HALT_REASON_DETAILS, ControllerAlert

CONTROLLER_EVENT_TYPES: Final[list[str]] = sorted(
    {
        "alarm_cleared",
        "alarm_lock",
        "controller_alarm",
        "hard_limit",
        *(details[1] for details in HALT_REASON_DETAILS.values()),
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakeraZ1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Makera Z1 controller event entity."""
    async_add_entities([MakeraZ1ControllerEvent(entry.runtime_data.coordinator)])


class MakeraZ1ControllerEvent(MakeraZ1Entity, EventEntity):
    """Publish each controller alarm transition once."""

    _attr_event_types = CONTROLLER_EVENT_TYPES
    _attr_icon = "mdi:alert-decagram-outline"
    _attr_translation_key = "controller"

    def __init__(self, coordinator: MakeraZ1Coordinator) -> None:
        """Initialize the event entity without replaying an existing alert."""
        super().__init__(coordinator, "controller_event")
        self._previous_alert = coordinator.data.alert if coordinator.data else None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Publish newly raised and cleared controller alerts."""
        alert = self.coordinator.data.alert if self.coordinator.data else None
        previous = self._previous_alert
        if alert is not None and (previous is None or alert.kind != previous.kind):
            self._trigger_event(alert.kind, _event_data(alert))
        elif alert is None and previous is not None:
            self._trigger_event("alarm_cleared", _event_data(previous))
        self._previous_alert = alert
        self.async_write_ha_state()


def _event_data(alert: ControllerAlert) -> dict[str, Any]:
    """Return useful automation data for one controller alert."""
    data: dict[str, Any] = {
        "alarm_type": alert.kind,
        "reason": alert.message,
    }
    if alert.code is not None:
        data["code"] = alert.code
    if alert.axis:
        data["axis"] = alert.axis
    if alert.direction:
        data["direction"] = alert.direction
    return data
