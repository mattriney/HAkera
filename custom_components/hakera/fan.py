"""Fan and powered-output platform for Makera Z1."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from homeassistant.components.fan import (
    FanEntity,
    FanEntityDescription,
    FanEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MakeraZ1ConfigEntry
from .const import DOMAIN
from .entity import MakeraZ1Entity
from .z1 import MakeraZ1Error, diagnostic_switch_is_active

PARALLEL_UPDATES = 1
POWER_STEP = 5


@dataclass(frozen=True, kw_only=True)
class MakeraZ1FanEntityDescription(FanEntityDescription):
    """Description for one Z1 feedback-backed powered output."""

    output_id: str
    state_field: str
    power_field: str
    default_percentage: int
    display_name: str


FAN_DESCRIPTIONS: tuple[MakeraZ1FanEntityDescription, ...] = (
    MakeraZ1FanEntityDescription(
        key="spindle_fan",
        translation_key="spindle_fan",
        icon="mdi:fan",
        output_id="spindle_fan",
        state_field="spindleFan",
        power_field="spindleFanPower",
        default_percentage=20,
        display_name="spindle fan",
    ),
    MakeraZ1FanEntityDescription(
        key="power_fan",
        translation_key="power_fan",
        icon="mdi:fan",
        output_id="power_fan",
        state_field="powerFan",
        power_field="powerFanPower",
        default_percentage=20,
        display_name="control-box fan",
    ),
    MakeraZ1FanEntityDescription(
        key="external_output",
        translation_key="external_output",
        icon="mdi:vacuum",
        output_id="external_output",
        state_field="externalOutput",
        power_field="externalOutputPower",
        default_percentage=5,
        display_name="external output",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakeraZ1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Makera Z1 powered outputs."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        MakeraZ1Fan(coordinator, description) for description in FAN_DESCRIPTIONS
    )


class MakeraZ1Fan(MakeraZ1Entity, FanEntity):
    """A Z1 fan-like output with controller feedback."""

    _attr_speed_count = 100 // POWER_STEP
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.TURN_ON
    )

    entity_description: MakeraZ1FanEntityDescription

    def __init__(self, coordinator, description: MakeraZ1FanEntityDescription) -> None:
        """Initialize a powered output."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the output state reported by the controller."""
        if not self.coordinator.data:
            return None
        return diagnostic_switch_is_active(
            self.coordinator.data.diagnostic_fields.get(
                self.entity_description.state_field
            )
        )

    @property
    def percentage(self) -> int | None:
        """Return the output power reported by the controller."""
        is_on = self.is_on
        if is_on is False:
            return 0
        if is_on is None or not self.coordinator.data:
            return None
        field = self.coordinator.data.diagnostic_fields.get(
            self.entity_description.power_field
        )
        if field is None or not field.known or field.value is None:
            return None
        return max(0, min(100, round(field.value)))

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the output at the requested or last reported power."""
        requested = percentage
        if requested is None:
            current = self.percentage
            requested = (
                current
                if current is not None and current > 0
                else self.entity_description.default_percentage
            )
        await self._async_set_state(True, _normalize_percentage(requested))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the output."""
        await self._async_set_state(False)

    async def async_set_percentage(self, percentage: int) -> None:
        """Set output power, using zero as off per Home Assistant semantics."""
        if percentage <= 0:
            await self._async_set_state(False)
            return
        await self._async_set_state(True, _normalize_percentage(percentage))

    async def _async_set_state(
        self, enabled: bool, percentage: int | None = None
    ) -> None:
        """Set and confirm one allowlisted output."""
        try:
            diagnostic = await self.coordinator.client.async_set_output(
                self.entity_description.output_id,
                enabled,
                percentage,
            )
        except (MakeraZ1Error, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="output_command_failed",
                translation_placeholders={
                    "output": self.entity_description.display_name,
                    "error": str(err),
                },
            ) from err
        self.coordinator.async_apply_diagnostic(diagnostic)


def _normalize_percentage(percentage: int | float) -> int:
    """Map a Home Assistant percentage to the nearest firmware step."""
    value = float(percentage)
    if not math.isfinite(value):
        raise ValueError("Output power must be a finite percentage.")
    value = max(1.0, min(100.0, value))
    return max(
        POWER_STEP,
        min(100, math.floor(value / POWER_STEP + 0.5) * POWER_STEP),
    )
