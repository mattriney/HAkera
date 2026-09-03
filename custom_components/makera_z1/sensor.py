"""Sensor platform for Makera Z1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_HOST,
    PERCENTAGE,
    UnitOfLength,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MakeraZ1ConfigEntry
from .entity import MakeraZ1Entity
from .z1 import MakeraZ1Snapshot

RPM = "rpm"
MILLIMETERS_PER_MINUTE = "mm/min"
DECIBELS_MILLIWATT = "dBm"
DEGREES = "deg"


@dataclass(frozen=True, kw_only=True)
class MakeraZ1SensorEntityDescription(SensorEntityDescription):
    """Description for Makera Z1 sensors."""

    value_fn: Callable[[MakeraZ1Snapshot], Any]


def _status_field(index: int, field: str) -> Callable[[MakeraZ1Snapshot], Any]:
    def value(snapshot: MakeraZ1Snapshot) -> Any:
        values = getattr(snapshot.status, field)
        if values is None or index >= len(values):
            return None
        return values[index]

    return value


def _diagnostic_value(field_id: str) -> Callable[[MakeraZ1Snapshot], Any]:
    def value(snapshot: MakeraZ1Snapshot) -> Any:
        item = snapshot.diagnostic_fields.get(field_id)
        if not item or not item.known:
            return None
        return item.value

    return value


SENSOR_DESCRIPTIONS: tuple[MakeraZ1SensorEntityDescription, ...] = (
    MakeraZ1SensorEntityDescription(
        key="machine_state",
        translation_key="machine_state",
        icon="mdi:state-machine",
        value_fn=lambda snapshot: snapshot.status.state,
    ),
    MakeraZ1SensorEntityDescription(
        key="alarm_reason",
        translation_key="alarm_reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:alert-circle-outline",
        value_fn=lambda snapshot: (
            snapshot.alert.message if snapshot.alert is not None else None
        ),
    ),
    MakeraZ1SensorEntityDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chip",
        value_fn=lambda snapshot: snapshot.identity.firmware_version,
    ),
    MakeraZ1SensorEntityDescription(
        key="filesystem_type",
        translation_key="filesystem_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:sd",
        value_fn=lambda snapshot: snapshot.identity.filesystem_type,
    ),
    MakeraZ1SensorEntityDescription(
        key="current_tool",
        translation_key="current_tool",
        icon="mdi:tools",
        value_fn=_status_field(0, "tool"),
    ),
    MakeraZ1SensorEntityDescription(
        key="feed_rate",
        translation_key="feed_rate",
        native_unit_of_measurement=MILLIMETERS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
        value_fn=_status_field(0, "feed"),
    ),
    MakeraZ1SensorEntityDescription(
        key="feed_override",
        translation_key="feed_override",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tune-variant",
        value_fn=lambda snapshot: snapshot.status.override,
    ),
    MakeraZ1SensorEntityDescription(
        key="spindle_current_rpm",
        translation_key="spindle_current_rpm",
        native_unit_of_measurement=RPM,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:rotate-right",
        value_fn=lambda snapshot: (
            snapshot.spindle_report.current_rpm
            if snapshot.spindle_report.current_rpm is not None
            else _status_field(0, "spindle")(snapshot)
        ),
    ),
    MakeraZ1SensorEntityDescription(
        key="spindle_target_rpm",
        translation_key="spindle_target_rpm",
        native_unit_of_measurement=RPM,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:target",
        value_fn=lambda snapshot: (
            snapshot.spindle_report.target_rpm
            if snapshot.spindle_report.target_rpm is not None
            else _status_field(1, "spindle")(snapshot)
        ),
    ),
    MakeraZ1SensorEntityDescription(
        key="spindle_scale",
        translation_key="spindle_scale",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tune",
        value_fn=_status_field(2, "spindle"),
    ),
    MakeraZ1SensorEntityDescription(
        key="spindle_pwm_value",
        translation_key="spindle_pwm_value",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:pulse",
        value_fn=lambda snapshot: snapshot.spindle_report.pwm_value,
    ),
    MakeraZ1SensorEntityDescription(
        key="spindle_temperature",
        translation_key="spindle_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_diagnostic_value("spindleTemperature"),
    ),
    MakeraZ1SensorEntityDescription(
        key="power_temperature",
        translation_key="power_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_diagnostic_value("powerTemperature"),
    ),
    MakeraZ1SensorEntityDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_diagnostic_value("rssi"),
    ),
    MakeraZ1SensorEntityDescription(
        key="homing_code",
        translation_key="homing_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:home-search",
        value_fn=lambda snapshot: snapshot.status.homing,
    ),
    MakeraZ1SensorEntityDescription(
        key="machine_x",
        translation_key="machine_x",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_status_field(0, "machine_position"),
    ),
    MakeraZ1SensorEntityDescription(
        key="machine_y",
        translation_key="machine_y",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_status_field(1, "machine_position"),
    ),
    MakeraZ1SensorEntityDescription(
        key="machine_z",
        translation_key="machine_z",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_status_field(2, "machine_position"),
    ),
    MakeraZ1SensorEntityDescription(
        key="machine_a",
        translation_key="machine_a",
        native_unit_of_measurement=DEGREES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_status_field(3, "machine_position"),
    ),
    MakeraZ1SensorEntityDescription(
        key="work_x",
        translation_key="work_x",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_status_field(0, "work_position"),
    ),
    MakeraZ1SensorEntityDescription(
        key="work_y",
        translation_key="work_y",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_status_field(1, "work_position"),
    ),
    MakeraZ1SensorEntityDescription(
        key="work_z",
        translation_key="work_z",
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_status_field(2, "work_position"),
    ),
    MakeraZ1SensorEntityDescription(
        key="work_a",
        translation_key="work_a",
        native_unit_of_measurement=DEGREES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_status_field(3, "work_position"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakeraZ1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Makera Z1 sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        MakeraZ1Sensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    )


class MakeraZ1Sensor(MakeraZ1Entity, SensorEntity):
    """A Makera Z1 sensor."""

    entity_description: MakeraZ1SensorEntityDescription

    def __init__(
        self,
        coordinator,
        description: MakeraZ1SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes for selected sensors."""
        if not self.coordinator.data:
            return None

        snapshot = self.coordinator.data
        if self.entity_description.key == "machine_state":
            return {
                CONF_HOST: self.coordinator.client.host,
                "raw_status": snapshot.status.raw,
                "model": snapshot.identity.model,
                "serial": snapshot.identity.serial,
            }
        if self.entity_description.key == "alarm_reason" and snapshot.alert:
            attributes = {"type": snapshot.alert.kind}
            if snapshot.alert.axis:
                attributes["axis"] = snapshot.alert.axis
            if snapshot.alert.direction:
                attributes["direction"] = snapshot.alert.direction
            return attributes
        return None
