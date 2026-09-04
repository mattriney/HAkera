"""Binary sensor platform for Makera Z1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MakeraZ1ConfigEntry
from .const import SPINDLE_AT_SPEED_TOLERANCE_PERCENT
from .coordinator import MakeraZ1Coordinator
from .entity import MakeraZ1Entity
from .z1 import (
    MakeraZ1Snapshot,
    diagnostic_switch_is_active,
    snapshot_is_alarmed,
    snapshot_spindle_running,
    snapshot_spindle_speed_deviation,
)


@dataclass(frozen=True, kw_only=True)
class MakeraZ1BinarySensorEntityDescription(BinarySensorEntityDescription):
    """Description for Makera Z1 binary sensors."""

    value_fn: Callable[[MakeraZ1Coordinator], bool | None]
    always_available: bool = False


def _diagnostic_active(
    field_id: str, *, active_low: bool = False
) -> Callable[[MakeraZ1Coordinator], bool | None]:
    def value(coordinator: MakeraZ1Coordinator) -> bool | None:
        snapshot = coordinator.data
        if not snapshot:
            return None
        return diagnostic_switch_is_active(
            snapshot.diagnostic_fields.get(field_id), active_low=active_low
        )

    return value


BINARY_SENSOR_DESCRIPTIONS: tuple[MakeraZ1BinarySensorEntityDescription, ...] = (
    MakeraZ1BinarySensorEntityDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda coordinator: coordinator.last_update_success,
        always_available=True,
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="alarm",
        translation_key="alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda coordinator: snapshot_is_alarmed(coordinator.data),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="soft_limit_alarm",
        translation_key="soft_limit_alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: _soft_limit_alarm(coordinator.data),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="spindle_running",
        translation_key="spindle_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda coordinator: snapshot_spindle_running(coordinator.data),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="machine_busy",
        translation_key="machine_busy",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda coordinator: _machine_busy(coordinator.data),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="controller_idle_clear",
        translation_key="controller_idle_clear",
        icon="mdi:check-circle-outline",
        value_fn=lambda coordinator: _controller_idle_clear(coordinator),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="spindle_at_speed",
        translation_key="spindle_at_speed",
        icon="mdi:speedometer-check",
        value_fn=lambda coordinator: _spindle_at_speed(coordinator.data),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="camera_streaming",
        translation_key="camera_streaming",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.camera_stream_clients > 0,
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="lid",
        translation_key="lid",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=_diagnostic_active("cover", active_low=True),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="emergency_stop",
        translation_key="emergency_stop",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_diagnostic_active("emergencyStop"),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="probe",
        translation_key="probe",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_diagnostic_active("probe"),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="tool_setter",
        translation_key="tool_setter",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_diagnostic_active("toolSetter"),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="external_input",
        translation_key="external_input",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_diagnostic_active("externalInput"),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="x_positive_limit",
        translation_key="x_positive_limit",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_diagnostic_active("xPositiveLimit"),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="y_positive_limit",
        translation_key="y_positive_limit",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_diagnostic_active("yPositiveLimit"),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="z_positive_limit",
        translation_key="z_positive_limit",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_diagnostic_active("zPositiveLimit"),
    ),
    MakeraZ1BinarySensorEntityDescription(
        key="a_positive_limit",
        translation_key="a_positive_limit",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_diagnostic_active("aPositiveLimit"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakeraZ1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Makera Z1 binary sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        MakeraZ1BinarySensor(coordinator, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class MakeraZ1BinarySensor(MakeraZ1Entity, BinarySensorEntity):
    """A Makera Z1 binary sensor."""

    entity_description: MakeraZ1BinarySensorEntityDescription

    def __init__(
        self,
        coordinator: MakeraZ1Coordinator,
        description: MakeraZ1BinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return whether the entity is available."""
        if self.entity_description.always_available:
            return True
        return super().available

    @property
    def is_on(self) -> bool | None:
        """Return the binary sensor state."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return parsed details for controller alarm entities."""
        if self.entity_description.key == "camera_streaming":
            return {"viewer_count": self.coordinator.camera_stream_clients}
        if self.entity_description.key == "spindle_at_speed":
            snapshot = self.coordinator.data
            if snapshot is None:
                return None
            return {
                "tolerance_percent": SPINDLE_AT_SPEED_TOLERANCE_PERCENT,
                "deviation_percent": snapshot_spindle_speed_deviation(snapshot),
            }
        if self.entity_description.key not in {"alarm", "soft_limit_alarm"}:
            return None
        snapshot = self.coordinator.data
        if not snapshot or not snapshot.alert:
            return None
        alert = snapshot.alert
        attributes: dict[str, Any] = {
            "reason": alert.message,
            "type": alert.kind,
        }
        if alert.axis:
            attributes["axis"] = alert.axis
        if alert.direction:
            attributes["direction"] = alert.direction
        if alert.code is not None:
            attributes["code"] = alert.code
        return attributes


_BUSY_STATES = {"check", "home", "homing", "hold", "jog", "probe", "probing", "run"}
_NOT_BUSY_STATES = {"alarm", "door", "halt", "idle", "sleep"}


def _machine_busy(snapshot: MakeraZ1Snapshot | None) -> bool | None:
    """Return activity only for recognized controller states."""
    if snapshot is None:
        return None
    state = snapshot.status.state.partition(":")[0].strip().lower()
    if state in _BUSY_STATES:
        return True
    if state in _NOT_BUSY_STATES:
        return False
    return None


def _controller_idle_clear(coordinator: MakeraZ1Coordinator) -> bool | None:
    """Return whether the connected controller is idle and fault-free."""
    snapshot = coordinator.data
    if snapshot is None:
        return None
    if not coordinator.last_update_success:
        return False
    if snapshot.status.state.strip().lower() != "idle":
        return False
    if snapshot_is_alarmed(snapshot):
        return False
    emergency_stop = diagnostic_switch_is_active(
        snapshot.diagnostic_fields.get("emergencyStop")
    )
    return not emergency_stop if emergency_stop is not None else None


def _spindle_at_speed(snapshot: MakeraZ1Snapshot | None) -> bool | None:
    """Return whether a running spindle is within its target tolerance."""
    if snapshot is None:
        return None
    running = snapshot_spindle_running(snapshot)
    if running is None:
        return None
    if not running:
        return False
    deviation = snapshot_spindle_speed_deviation(snapshot)
    if deviation is None:
        return None
    return deviation <= SPINDLE_AT_SPEED_TOLERANCE_PERCENT


def _soft_limit_alarm(snapshot: MakeraZ1Snapshot | None) -> bool | None:
    """Return whether the active controller alert is a soft-limit event."""
    if snapshot is None:
        return None
    return snapshot.alert is not None and snapshot.alert.kind == "soft_limit"
