"""Config flow for the Makera Z1 integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .z1 import MakeraZ1Client, MakeraZ1ConnectionError, MakeraZ1Error


class MakeraZ1ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Makera Z1."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
            try:
                serial = await _async_validate_host(self.hass, host)
            except ValueError:
                errors["base"] = "invalid_host"
            except MakeraZ1ConnectionError:
                errors["base"] = "cannot_connect"
            except MakeraZ1Error:
                errors["base"] = "cannot_identify"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=f"Makera Z1 {serial[-6:]}",
                    data={CONF_HOST: host},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )


async def _async_validate_host(hass: HomeAssistant, host: str) -> str:
    """Validate the host and return the stable controller serial number."""
    client = MakeraZ1Client(host)
    snapshot = await client.async_fetch_snapshot(include_identity=True)
    serial = snapshot.identity.serial
    if not serial:
        raise MakeraZ1Error("The controller did not return a serial number.")
    return serial
