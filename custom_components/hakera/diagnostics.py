"""Diagnostics support for Makera Z1."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import MakeraZ1ConfigEntry

TO_REDACT = {
    CONF_HOST,
    "configuration_url",
    "host",
    "serial",
    "serial_number",
    "unique_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: MakeraZ1ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data.coordinator
    snapshot = coordinator.data

    data: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "unique_id": entry.unique_id,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "last_exception": str(coordinator.last_exception)
        if coordinator.last_exception
        else None,
        "snapshot": snapshot.as_diagnostics() if snapshot else None,
    }
    return async_redact_data(data, TO_REDACT)
