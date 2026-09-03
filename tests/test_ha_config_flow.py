"""Test the Makera Z1 Home Assistant config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hakera.const import DOMAIN
from custom_components.hakera.z1 import MakeraZ1ConnectionError, MakeraZ1Error

HOST = "192.0.2.10"
SERIAL = "Z1P000000X000001"


async def test_user_flow_creates_unique_config_entry(hass: HomeAssistant) -> None:
    """Test successful setup through the user-facing flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    setup_entry = AsyncMock(return_value=True)
    with (
        patch(
            "custom_components.hakera.config_flow._async_validate_host",
            AsyncMock(return_value=SERIAL),
        ),
        patch("custom_components.hakera.async_setup_entry", setup_entry),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Makera Z1 000001"
    assert result["data"] == {CONF_HOST: HOST}
    assert result["result"].unique_id == SERIAL
    setup_entry.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "flow_error"),
    [
        (ValueError("invalid"), "invalid_host"),
        (MakeraZ1ConnectionError("offline"), "cannot_connect"),
        (MakeraZ1Error("missing serial"), "cannot_identify"),
    ],
)
async def test_user_flow_reports_validation_errors(
    hass: HomeAssistant,
    error: Exception,
    flow_error: str,
) -> None:
    """Test that setup failures stay in the form with a useful error."""
    with patch(
        "custom_components.hakera.config_flow._async_validate_host",
        AsyncMock(side_effect=error),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: HOST},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": flow_error}


async def test_duplicate_device_is_rejected_and_host_is_updated(
    hass: HomeAssistant,
) -> None:
    """Test stable serial-number deduplication and IP-address updates."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Makera Z1 000001",
        data={CONF_HOST: "192.0.2.99"},
        unique_id=SERIAL,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.hakera.config_flow._async_validate_host",
        AsyncMock(return_value=SERIAL),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: HOST},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {CONF_HOST: HOST}
