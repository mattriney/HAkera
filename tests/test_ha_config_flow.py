"""Test the Makera Z1 Home Assistant config flow."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hakera.config_flow import _async_validate_host
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


async def test_reconfigure_updates_host_and_reloads_entry(
    hass: HomeAssistant,
) -> None:
    """Test changing the host after verifying the controller identity."""
    old_host = "192.0.2.99"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Makera Z1 000001",
        data={CONF_HOST: old_host},
        unique_id=SERIAL,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["data_schema"]({}) == {CONF_HOST: old_host}

    reload_entry = AsyncMock(return_value=True)
    with (
        patch(
            "custom_components.hakera.config_flow._async_validate_host",
            AsyncMock(return_value=SERIAL),
        ),
        patch.object(hass.config_entries, "async_reload", reload_entry),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_HOST: HOST}
    reload_entry.assert_awaited_once_with(entry.entry_id)


async def test_reconfigure_rejects_a_different_machine(
    hass: HomeAssistant,
) -> None:
    """Test that reconfiguration cannot replace an entry with another Z1."""
    old_host = "192.0.2.99"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Makera Z1 000001",
        data={CONF_HOST: old_host},
        unique_id=SERIAL,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.hakera.config_flow._async_validate_host",
        AsyncMock(return_value="Z1P000000X000002"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
            data={CONF_HOST: HOST},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "wrong_device"}
    assert entry.data == {CONF_HOST: old_host}


@pytest.mark.parametrize(
    ("error", "flow_error"),
    [
        (ValueError("invalid"), "invalid_host"),
        (MakeraZ1ConnectionError("offline"), "cannot_connect"),
        (MakeraZ1Error("missing serial"), "cannot_identify"),
    ],
)
async def test_reconfigure_reports_validation_errors(
    hass: HomeAssistant,
    error: Exception,
    flow_error: str,
) -> None:
    """Test that a failed host update leaves the existing entry untouched."""
    old_host = "192.0.2.99"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Makera Z1 000001",
        data={CONF_HOST: old_host},
        unique_id=SERIAL,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.hakera.config_flow._async_validate_host",
        AsyncMock(side_effect=error),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
            data={CONF_HOST: HOST},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": flow_error}
    assert entry.data == {CONF_HOST: old_host}


@pytest.mark.parametrize(
    ("serial", "expected_error"),
    [(SERIAL, None), (None, MakeraZ1Error)],
)
async def test_validate_host_requires_a_serial_number(
    hass: HomeAssistant,
    serial: str | None,
    expected_error: type[Exception] | None,
) -> None:
    """Test direct controller validation and missing identity handling."""
    client = MagicMock()
    client.async_fetch_snapshot = AsyncMock(
        return_value=SimpleNamespace(identity=SimpleNamespace(serial=serial))
    )
    client.async_close = AsyncMock()
    session = object()

    with (
        patch(
            "custom_components.hakera.config_flow.async_get_clientsession",
            return_value=session,
        ) as get_session,
        patch(
            "custom_components.hakera.config_flow.MakeraZ1Client",
            return_value=client,
        ) as client_class,
    ):
        if expected_error is None:
            assert await _async_validate_host(hass, HOST) == SERIAL
        else:
            with pytest.raises(expected_error):
                await _async_validate_host(hass, HOST)

    get_session.assert_called_once_with(hass)
    client_class.assert_called_once_with(HOST, session=session)
    client.async_fetch_snapshot.assert_awaited_once_with(include_identity=True)
    client.async_close.assert_awaited_once_with()


async def test_validate_host_closes_client_after_transport_failure(
    hass: HomeAssistant,
) -> None:
    """Test temporary config-flow clients close when probing fails."""
    client = MagicMock()
    client.async_fetch_snapshot = AsyncMock(
        side_effect=MakeraZ1ConnectionError("offline")
    )
    client.async_close = AsyncMock()

    with (
        patch(
            "custom_components.hakera.config_flow.async_get_clientsession",
            return_value=object(),
        ),
        patch(
            "custom_components.hakera.config_flow.MakeraZ1Client",
            return_value=client,
        ),
        pytest.raises(MakeraZ1ConnectionError, match="offline"),
    ):
        await _async_validate_host(hass, HOST)

    client.async_close.assert_awaited_once_with()
