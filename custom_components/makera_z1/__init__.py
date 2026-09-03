"""The Makera Z1 integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import PLATFORMS
from .coordinator import MakeraZ1Coordinator
from .z1 import MakeraZ1Client


@dataclass(slots=True)
class MakeraZ1RuntimeData:
    """Runtime data stored on the config entry."""

    client: MakeraZ1Client
    coordinator: MakeraZ1Coordinator


type MakeraZ1ConfigEntry = ConfigEntry[MakeraZ1RuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: MakeraZ1ConfigEntry) -> bool:
    """Set up Makera Z1 from a config entry."""
    client = MakeraZ1Client(
        entry.data[CONF_HOST],
        session=async_get_clientsession(hass),
    )
    coordinator = MakeraZ1Coordinator(hass, entry, client)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = MakeraZ1RuntimeData(client=client, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MakeraZ1ConfigEntry) -> bool:
    """Unload a Makera Z1 config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.client.async_close()
    return unload_ok
