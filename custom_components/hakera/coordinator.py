"""Coordinator for Makera Z1 data updates."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .z1 import (
    DiagnosticStatus,
    MakeraZ1Client,
    MakeraZ1ConnectionError,
    MakeraZ1Snapshot,
    map_diagnostic_fields,
)

_LOGGER = logging.getLogger(__name__)


class MakeraZ1Coordinator(DataUpdateCoordinator[MakeraZ1Snapshot]):
    """Coordinate polling the Makera Z1."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: MakeraZ1Client,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
            always_update=False,
        )
        self.entry = config_entry
        self.client = client

    @property
    def device_identifier(self) -> str:
        """Return the stable device identifier."""
        if self.data and self.data.identity.serial:
            return self.data.identity.serial
        return self.entry.unique_id or self.entry.entry_id

    async def _async_update_data(self) -> MakeraZ1Snapshot:
        """Fetch one device snapshot."""
        try:
            async with asyncio.timeout(5):
                return await self.client.async_fetch_snapshot()
        except MakeraZ1ConnectionError as err:
            raise UpdateFailed(str(err)) from err

    @callback
    def async_apply_diagnostic(self, diagnostic: DiagnosticStatus) -> None:
        """Apply freshly confirmed output feedback without another TCP poll."""
        if self.data is None:
            return
        self.async_set_updated_data(
            replace(
                self.data,
                diagnostic=diagnostic,
                diagnostic_fields=map_diagnostic_fields(diagnostic),
            )
        )
