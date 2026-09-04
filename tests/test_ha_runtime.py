"""Test Makera Z1 behavior through Home Assistant's public interfaces."""

from __future__ import annotations

import json
from collections.abc import Sequence
from unittest.mock import AsyncMock, MagicMock, call, patch

from homeassistant.components import camera
from homeassistant.components.diagnostics import REDACTED
from homeassistant.components.fan import ATTR_PERCENTAGE, SERVICE_SET_PERCENTAGE
from homeassistant.components.select import ATTR_OPTION, SERVICE_SELECT_OPTION
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    SERVICE_TURN_OFF,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hakera.camera import MakeraZ1Camera
from custom_components.hakera.const import DOMAIN
from custom_components.hakera.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.hakera.z1 import (
    MakeraZ1Snapshot,
    parse_diagnostic_packet,
)

HOST = "192.0.2.10"
SERIAL = "Z1P000000X000001"
TITLE = "Makera Z1 000001"


async def _async_setup_entry(
    hass: HomeAssistant,
    snapshots: MakeraZ1Snapshot | Sequence[MakeraZ1Snapshot],
    *,
    add_obsolete_light_sensor: bool = False,
) -> tuple[MockConfigEntry, AsyncMock]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=TITLE,
        data={CONF_HOST: HOST},
        unique_id=SERIAL,
    )
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    if add_obsolete_light_sensor:
        registry.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            f"{SERIAL}_work_light",
            suggested_object_id="obsolete_work_light_feedback",
            config_entry=entry,
        )

    fetch = AsyncMock(
        side_effect=list(snapshots) if isinstance(snapshots, Sequence) else None,
        return_value=snapshots if not isinstance(snapshots, Sequence) else None,
    )
    with patch("custom_components.hakera.MakeraZ1Client.async_fetch_snapshot", fetch):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    return entry, fetch


def _entity_id(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


def _state(hass: HomeAssistant, entity_id: str) -> State:
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def test_setup_exposes_entities_and_removes_obsolete_feedback_sensor(
    hass: HomeAssistant,
    idle_snapshot: MakeraZ1Snapshot,
) -> None:
    """Test setup, entity states, registry cleanup, and unload."""
    entry, fetch = await _async_setup_entry(
        hass, idle_snapshot, add_obsolete_light_sensor=True
    )
    registry = er.async_get(hass)

    assert fetch.await_count == 1
    assert (
        registry.async_get_entity_id("binary_sensor", DOMAIN, f"{SERIAL}_work_light")
        is None
    )

    connected = hass.states.get(
        _entity_id(hass, "binary_sensor", f"{SERIAL}_connected")
    )
    assert connected is not None
    assert connected.state == STATE_ON

    machine_state = hass.states.get(
        _entity_id(hass, "sensor", f"{SERIAL}_machine_state")
    )
    assert machine_state is not None
    assert machine_state.state == "Idle"

    assert (
        _state(hass, _entity_id(hass, "binary_sensor", f"{SERIAL}_machine_busy")).state
        == STATE_OFF
    )
    assert (
        _state(
            hass,
            _entity_id(hass, "binary_sensor", f"{SERIAL}_controller_idle_clear"),
        ).state
        == STATE_ON
    )
    assert (
        _state(
            hass, _entity_id(hass, "binary_sensor", f"{SERIAL}_spindle_at_speed")
        ).state
        == STATE_OFF
    )
    camera_streaming = _state(
        hass, _entity_id(hass, "binary_sensor", f"{SERIAL}_camera_streaming")
    )
    assert camera_streaming.state == STATE_OFF
    assert camera_streaming.attributes["viewer_count"] == 0
    entry.runtime_data.coordinator.async_update_camera_stream_clients(1)
    await hass.async_block_till_done()
    camera_streaming = _state(hass, camera_streaming.entity_id)
    assert camera_streaming.state == STATE_ON
    assert camera_streaming.attributes["viewer_count"] == 1
    entry.runtime_data.coordinator.async_update_camera_stream_clients(-1)
    await hass.async_block_till_done()
    assert _state(hass, camera_streaming.entity_id).state == STATE_OFF
    assert (
        _state(
            hass, _entity_id(hass, "sensor", f"{SERIAL}_spindle_speed_deviation")
        ).state
        == "unknown"
    )

    work_light = hass.states.get(
        _entity_id(hass, "light", f"{SERIAL}_work_light_control")
    )
    assert work_light is not None
    assert work_light.state == STATE_ON

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_existing_alarm_is_not_replayed_as_a_new_event(
    hass: HomeAssistant,
    soft_limit_snapshot: MakeraZ1Snapshot,
) -> None:
    """Test setup exposes current alarm state without inventing an event."""
    entry, _ = await _async_setup_entry(hass, soft_limit_snapshot)
    event_id = _entity_id(hass, "event", f"{SERIAL}_controller_event")

    assert _state(hass, event_id).state == "unknown"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_soft_limit_status_updates_and_clears(
    hass: HomeAssistant,
    idle_snapshot: MakeraZ1Snapshot,
    soft_limit_snapshot: MakeraZ1Snapshot,
) -> None:
    """Test an H:10 alarm through coordinator-backed HA entities."""
    entry, fetch = await _async_setup_entry(
        hass, [idle_snapshot, soft_limit_snapshot, idle_snapshot]
    )
    coordinator = entry.runtime_data.coordinator
    soft_limit_id = _entity_id(hass, "binary_sensor", f"{SERIAL}_soft_limit_alarm")
    reason_id = _entity_id(hass, "sensor", f"{SERIAL}_alarm_reason")
    event_id = _entity_id(hass, "event", f"{SERIAL}_controller_event")

    assert _state(hass, soft_limit_id).state == STATE_OFF
    assert _state(hass, reason_id).state == "unknown"
    assert _state(hass, event_id).state == "unknown"

    with patch("custom_components.hakera.MakeraZ1Client.async_fetch_snapshot", fetch):
        await coordinator.async_refresh()

    alarm_state = _state(hass, soft_limit_id)
    assert alarm_state.state == STATE_ON
    assert alarm_state.attributes["reason"] == "Soft Limit Triggered"
    assert alarm_state.attributes["type"] == "soft_limit"
    assert alarm_state.attributes["code"] == 10
    assert "axis" not in alarm_state.attributes
    assert _state(hass, reason_id).state == "Soft Limit Triggered"
    event_state = _state(hass, event_id)
    assert event_state.attributes["event_type"] == "soft_limit"
    assert event_state.attributes["alarm_type"] == "soft_limit"
    assert event_state.attributes["reason"] == "Soft Limit Triggered"
    assert event_state.attributes["code"] == 10

    with patch("custom_components.hakera.MakeraZ1Client.async_fetch_snapshot", fetch):
        await coordinator.async_refresh()

    assert _state(hass, soft_limit_id).state == STATE_OFF
    assert _state(hass, reason_id).state == "unknown"
    cleared_event = _state(hass, event_id)
    assert cleared_event.attributes["event_type"] == "alarm_cleared"
    assert cleared_event.attributes["alarm_type"] == "soft_limit"
    assert cleared_event.attributes["code"] == 10


async def test_accessory_services_use_feedback(
    hass: HomeAssistant,
    idle_snapshot: MakeraZ1Snapshot,
) -> None:
    """Test light and powered outputs through Home Assistant services."""
    entry, _ = await _async_setup_entry(hass, idle_snapshot)
    client = entry.runtime_data.client

    light_id = _entity_id(hass, "light", f"{SERIAL}_work_light_control")
    light_off = parse_diagnostic_packet(
        "{S:0,10000,0,0,26,23|V:1,31|F:0,0|G:0,0,0,0,0|"
        "E:0,0,0,0,0,0,1,0|P:0,0|I:0|RSSI:-63}"
    )
    client.async_set_work_light = AsyncMock(return_value=light_off)
    await hass.services.async_call(
        "light",
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: light_id},
        blocking=True,
    )
    client.async_set_work_light.assert_awaited_once_with(False)
    assert _state(hass, light_id).state == STATE_OFF

    fan_id = _entity_id(hass, "fan", f"{SERIAL}_spindle_fan")
    fan_on = parse_diagnostic_packet(
        "{S:0,10000,0,0,26,23|V:1,31|F:1,35|G:0,0,0,0,0|"
        "E:0,0,0,0,0,0,1,0|P:0,0|I:0|RSSI:-63}"
    )
    client.async_set_output = AsyncMock(return_value=fan_on)
    await hass.services.async_call(
        "fan",
        SERVICE_SET_PERCENTAGE,
        {ATTR_ENTITY_ID: fan_id, ATTR_PERCENTAGE: 35},
        blocking=True,
    )
    client.async_set_output.assert_awaited_once_with("spindle_fan", True, 35)
    assert _state(hass, fan_id).state == STATE_ON
    assert _state(hass, fan_id).attributes[ATTR_PERCENTAGE] == 35


async def test_camera_still_and_resolution_services(
    hass: HomeAssistant,
    idle_snapshot: MakeraZ1Snapshot,
) -> None:
    """Test camera and resolution access through Home Assistant interfaces."""
    entry, _ = await _async_setup_entry(hass, idle_snapshot)
    client = entry.runtime_data.client

    camera_id = _entity_id(hass, "camera", f"{SERIAL}_camera")
    jpeg = b"\xff\xd8\xff\xd9"
    client.async_get_camera_image = AsyncMock(return_value=jpeg)
    image = await camera.async_get_image(hass, camera_id)
    assert image.content_type == "image/jpeg"
    assert image.content == jpeg
    client.async_get_camera_image.assert_awaited_once_with()

    resolution_id = _entity_id(hass, "select", f"{SERIAL}_camera_resolution")
    client.async_set_camera_resolution = AsyncMock()
    await hass.services.async_call(
        "select",
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: resolution_id, ATTR_OPTION: "640x480"},
        blocking=True,
    )
    client.async_set_camera_resolution.assert_awaited_once_with(10)


async def test_camera_mjpeg_stream_adapter(
    hass: HomeAssistant,
    idle_snapshot: MakeraZ1Snapshot,
) -> None:
    """Test relaying upstream JPEGs as a finite MJPEG response."""
    entry, _ = await _async_setup_entry(hass, idle_snapshot)
    entity = MakeraZ1Camera(entry.runtime_data.coordinator)
    update_stream_clients = MagicMock(
        wraps=entry.runtime_data.coordinator.async_update_camera_stream_clients
    )
    entry.runtime_data.coordinator.async_update_camera_stream_clients = (
        update_stream_clients
    )
    frames = (b"first-jpeg", b"second-jpeg")

    async def camera_frames():
        for frame in frames:
            yield frame

    entry.runtime_data.client.async_camera_frames = MagicMock(side_effect=camera_frames)
    request = MagicMock()
    response = MagicMock()
    response.headers = {}
    response.prepare = AsyncMock()
    response.write = AsyncMock()
    response.write_eof = AsyncMock()

    with patch(
        "custom_components.hakera.camera.web.StreamResponse",
        return_value=response,
    ):
        result = await entity.handle_async_mjpeg_stream(request)

    assert result is response
    response.prepare.assert_awaited_once_with(request)
    assert response.write.await_args_list == [
        call(_mjpeg_part(frames[0])),
        call(_mjpeg_part(frames[0])),
        call(_mjpeg_part(frames[1])),
    ]
    response.write_eof.assert_awaited_once_with()
    assert entity.is_streaming is False
    assert update_stream_clients.call_args_list == [call(1), call(-1)]
    assert entry.runtime_data.coordinator.camera_stream_clients == 0


def _mjpeg_part(frame: bytes) -> bytes:
    return (
        b"--frameboundary\r\n"
        b"Content-Type: image/jpeg\r\n"
        + f"Content-Length: {len(frame)}\r\n\r\n".encode()
        + frame
        + b"\r\n"
    )


async def test_diagnostics_redact_device_identity(
    hass: HomeAssistant,
    idle_snapshot: MakeraZ1Snapshot,
) -> None:
    """Test diagnostics contain useful telemetry without host or serial data."""
    entry, _ = await _async_setup_entry(hass, idle_snapshot)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["unique_id"] == REDACTED
    assert diagnostics["entry"]["data"][CONF_HOST] == REDACTED
    assert diagnostics["snapshot"]["identity"]["serial"] == REDACTED
    assert diagnostics["last_update_success"] is True
    serialized = json.dumps(diagnostics)
    assert HOST not in serialized
    assert SERIAL not in serialized
