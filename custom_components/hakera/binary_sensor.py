"""Binary sensor platform for Makera Z1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MakeraZ1ConfigEntry
from .coordinator import MakeraZ1Coordinator
from .entity import MakeraZ1Entity
from .z1 import (
    MakeraZ1Snapshot,
    diagnostic_switch_is_active,
    snapshot_is_alarmed,
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
        value_fn=lambda coordinator: _spindle_running(coordinator.data),
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
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Return parsed details for controller alarm entities."""
        if self.entity_description.key not in {"alarm", "soft_limit_alarm"}:
            return None
        snapshot = self.coordinator.data
        if not snapshot or not snapshot.alert:
            return None
        alert = snapshot.alert
        attributes = {"reason": alert.message, "type": alert.kind}
        if alert.axis:
            attributes["axis"] = alert.axis
        if alert.direction:
            attributes["direction"] = alert.direction
        if alert.code is not None:
            attributes["code"] = alert.code
        return attributes


def _spindle_running(snapshot: MakeraZ1Snapshot | None) -> bool | None:
    """Infer spindle-running state from read-only status."""
    if not snapshot:
        return None

    if snapshot.spindle_report.state:
        return snapshot.spindle_report.state.lower() in {"on", "running", "start"}

    if snapshot.spindle_report.current_rpm is not None:
        return snapshot.spindle_report.current_rpm > 0

    if snapshot.status.spindle and snapshot.status.spindle[0] is not None:
        return snapshot.status.spindle[0] > 0

    return None


def _soft_limit_alarm(snapshot: MakeraZ1Snapshot | None) -> bool | None:
    """Return whether the active controller alert is a soft-limit event."""
    if snapshot is None:
        return None
    return snapshot.alert is not None and snapshot.alert.kind == "soft_limit"
