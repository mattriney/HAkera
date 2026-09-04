"""Test Home Assistant entity and coordinator recovery behavior."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from homeassistant.const import CONF_HOST
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hakera.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    MakeraZ1BinarySensor,
    _controller_idle_clear,
    _diagnostic_active,
    _machine_busy,
    _soft_limit_alarm,
    _spindle_at_speed,
)
from custom_components.hakera.camera import MakeraZ1Camera
from custom_components.hakera.const import DOMAIN
from custom_components.hakera.coordinator import MakeraZ1Coordinator
from custom_components.hakera.event import _event_data
from custom_components.hakera.fan import (
    FAN_DESCRIPTIONS,
    MakeraZ1Fan,
    _normalize_percentage,
)
from custom_components.hakera.light import MakeraZ1WorkLight
from custom_components.hakera.select import MakeraZ1CameraResolution
from custom_components.hakera.sensor import (
    SENSOR_DESCRIPTIONS,
    MakeraZ1Sensor,
    _diagnostic_value,
    _status_field,
)
from custom_components.hakera.z1 import (
    ControllerAlert,
    DiagnosticField,
    MakeraZ1CameraBusyError,
    MakeraZ1ConnectionError,
    MakeraZ1ResponseError,
    SpindleReport,
    parse_diagnostic_packet,
    snapshot_spindle_running,
    snapshot_spindle_speed_deviation,
)

HOST = "192.0.2.10"
SERIAL = "Z1P000000X000001"


@pytest.fixture
def coordinator(hass, idle_snapshot) -> MakeraZ1Coordinator:
    """Return a coordinator with stable mocked controller I/O."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Makera Z1 000001",
        data={CONF_HOST: HOST},
        unique_id=SERIAL,
    )
    client = MagicMock()
    client.host = HOST
    client.camera_resolution_option = None
    coordinator = MakeraZ1Coordinator(hass, entry, client)
    coordinator.data = idle_snapshot
    coordinator.last_update_success = True
    return coordinator


def _description(descriptions, key):
    return next(description for description in descriptions if description.key == key)


def test_binary_sensor_fallbacks_and_alarm_attributes(
    coordinator,
    idle_snapshot,
) -> None:
    """Test missing telemetry, spindle fallbacks, and detailed alarms."""
    coordinator.data = None
    assert _diagnostic_active("cover")(coordinator) is None
    assert snapshot_spindle_running(None) is None
    assert _soft_limit_alarm(None) is None

    assert snapshot_spindle_running(
        replace(idle_snapshot, spindle_report=SpindleReport(state="running"))
    )
    assert not snapshot_spindle_running(
        replace(idle_snapshot, spindle_report=SpindleReport(state="off"))
    )
    assert snapshot_spindle_running(
        replace(idle_snapshot, spindle_report=SpindleReport(current_rpm=1))
    )
    assert snapshot_spindle_running(
        replace(
            idle_snapshot,
            spindle_report=SpindleReport(),
            status=replace(idle_snapshot.status, spindle=(1.0,)),
        )
    )
    assert (
        snapshot_spindle_running(
            replace(
                idle_snapshot,
                spindle_report=SpindleReport(),
                status=replace(idle_snapshot.status, spindle=None),
            )
        )
        is None
    )

    alert = ControllerAlert(
        message="ALARM: Hard limit X+",
        kind="hard_limit",
        axis="X",
        direction="positive",
        code=21,
    )
    coordinator.data = replace(idle_snapshot, alert=alert)
    alarm = MakeraZ1BinarySensor(
        coordinator, _description(BINARY_SENSOR_DESCRIPTIONS, "alarm")
    )
    assert alarm.extra_state_attributes == {
        "reason": alert.message,
        "type": "hard_limit",
        "axis": "X",
        "direction": "positive",
        "code": 21,
    }


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("Idle", False),
        ("Alarm", False),
        ("Run", True),
        ("Hold:0", True),
        ("Jog", True),
        ("Home", True),
        ("Unknown future state", None),
    ],
)
def test_machine_busy_classifies_only_known_states(
    idle_snapshot, state, expected
) -> None:
    """Test activity is not guessed for unknown firmware states."""
    snapshot = replace(idle_snapshot, status=replace(idle_snapshot.status, state=state))
    assert _machine_busy(snapshot) is expected


def test_controller_clear_and_spindle_speed_helpers(coordinator, idle_snapshot) -> None:
    """Test automation-focused composite state and spindle telemetry."""
    assert _controller_idle_clear(coordinator)
    coordinator.last_update_success = False
    assert not _controller_idle_clear(coordinator)
    coordinator.last_update_success = True
    coordinator.data = replace(
        idle_snapshot, status=replace(idle_snapshot.status, state="Home")
    )
    assert not _controller_idle_clear(coordinator)

    running = replace(
        idle_snapshot,
        spindle_report=SpindleReport(
            state="running", current_rpm=9500, target_rpm=10000
        ),
    )
    assert snapshot_spindle_speed_deviation(running) == 5
    assert _spindle_at_speed(running)

    slow = replace(
        running,
        spindle_report=replace(running.spindle_report, current_rpm=8900),
    )
    assert snapshot_spindle_speed_deviation(slow) == 11
    assert not _spindle_at_speed(slow)
    assert snapshot_spindle_speed_deviation(idle_snapshot) is None

    detailed_alert = ControllerAlert(
        message="ALARM: Hard limit X-",
        kind="hard_limit",
        axis="X",
        direction="negative",
        code=21,
    )
    assert _event_data(detailed_alert) == {
        "alarm_type": "hard_limit",
        "reason": "ALARM: Hard limit X-",
        "code": 21,
        "axis": "X",
        "direction": "negative",
    }


@pytest.mark.asyncio
async def test_fan_defaults_rounding_off_and_error_translation(
    coordinator,
    idle_snapshot,
) -> None:
    """Test Home Assistant fan semantics and controller error translation."""
    fan = MakeraZ1Fan(coordinator, _description(FAN_DESCRIPTIONS, "spindle_fan"))
    coordinator.async_apply_diagnostic = MagicMock()
    diagnostic = parse_diagnostic_packet("{F:1,35}")
    coordinator.client.async_set_output = AsyncMock(return_value=diagnostic)

    coordinator.data = None
    assert fan.is_on is None
    assert fan.percentage is None
    coordinator.data = idle_snapshot
    assert fan.is_on is False
    assert fan.percentage == 0

    await fan.async_turn_on()
    await fan.async_turn_on(percentage=12)
    await fan.async_set_percentage(33)
    await fan.async_set_percentage(0)
    await fan.async_turn_off()
    assert coordinator.client.async_set_output.await_args_list == [
        call("spindle_fan", True, 20),
        call("spindle_fan", True, 10),
        call("spindle_fan", True, 35),
        call("spindle_fan", False, None),
        call("spindle_fan", False, None),
    ]
    assert coordinator.async_apply_diagnostic.call_count == 5

    coordinator.client.async_set_output = AsyncMock(
        side_effect=MakeraZ1ResponseError("not confirmed")
    )
    with pytest.raises(HomeAssistantError) as error:
        await fan.async_turn_on()
    assert error.value.translation_key == "output_command_failed"
    assert error.value.translation_placeholders == {
        "output": "spindle fan",
        "error": "not confirmed",
    }
    with pytest.raises(ValueError, match="finite"):
        _normalize_percentage(float("nan"))

    fields = dict(idle_snapshot.diagnostic_fields)
    fields["spindleFan"] = DiagnosticField("fan", "switch", None, True, 1)
    fields["spindleFanPower"] = DiagnosticField("power", "number", "%", False, None)
    coordinator.data = replace(idle_snapshot, diagnostic_fields=fields)
    assert fan.percentage is None


@pytest.mark.asyncio
async def test_light_and_resolution_error_translation(coordinator) -> None:
    """Test light and resolution service validation and command errors."""
    light = MakeraZ1WorkLight(coordinator)
    coordinator.async_apply_diagnostic = MagicMock()
    diagnostic = parse_diagnostic_packet("{G:1,0,0,0,0}")
    coordinator.client.async_set_work_light = AsyncMock(return_value=diagnostic)
    await light.async_turn_on()
    coordinator.client.async_set_work_light.assert_awaited_once_with(True)
    coordinator.async_apply_diagnostic.assert_called_once_with(diagnostic)

    coordinator.client.async_set_work_light = AsyncMock(
        side_effect=MakeraZ1ConnectionError("offline")
    )
    with pytest.raises(HomeAssistantError) as error:
        await light.async_turn_off()
    assert error.value.translation_key == "work_light_command_failed"
    assert error.value.translation_placeholders == {"error": "offline"}

    coordinator.data = None
    assert light.is_on is None

    resolution = MakeraZ1CameraResolution(coordinator)
    with pytest.raises(ServiceValidationError) as error:
        await resolution.async_select_option("999x999")
    assert error.value.translation_key == "invalid_camera_resolution"
    assert error.value.translation_placeholders == {"option": "999x999"}
    coordinator.client.async_set_camera_resolution = AsyncMock(
        side_effect=MakeraZ1ResponseError("rejected")
    )
    with pytest.raises(HomeAssistantError) as error:
        await resolution.async_select_option("640x480")
    assert error.value.translation_key == "camera_resolution_command_failed"
    assert error.value.translation_placeholders == {"error": "rejected"}


def test_sensor_missing_data_and_alarm_attributes(coordinator, idle_snapshot) -> None:
    """Test sensors omit unavailable and inapplicable values cleanly."""
    assert _status_field(9, "feed")(idle_snapshot) is None
    assert (
        _status_field(0, "machine_position")(
            replace(
                idle_snapshot,
                status=replace(idle_snapshot.status, machine_position=None),
            )
        )
        is None
    )
    assert _diagnostic_value("missing")(idle_snapshot) is None

    state_sensor = MakeraZ1Sensor(
        coordinator, _description(SENSOR_DESCRIPTIONS, "machine_state")
    )
    coordinator.data = None
    assert state_sensor.native_value is None
    assert state_sensor.extra_state_attributes is None

    alert = ControllerAlert(
        message="Hard limit",
        kind="hard_limit",
        axis="Y",
        direction="negative",
    )
    coordinator.data = replace(idle_snapshot, alert=alert)
    alarm_sensor = MakeraZ1Sensor(
        coordinator, _description(SENSOR_DESCRIPTIONS, "alarm_reason")
    )
    assert alarm_sensor.extra_state_attributes == {
        "type": "hard_limit",
        "axis": "Y",
        "direction": "negative",
    }


@pytest.mark.asyncio
async def test_coordinator_fallback_error_and_empty_diagnostic(
    coordinator,
) -> None:
    """Test fallback identity, connection errors, and pre-data feedback."""
    coordinator.data = None
    assert coordinator.device_identifier == SERIAL
    coordinator.async_apply_diagnostic(parse_diagnostic_packet("{F:1,20}"))
    assert coordinator.data is None

    coordinator.client.async_fetch_snapshot = AsyncMock(
        side_effect=MakeraZ1ConnectionError("offline")
    )
    with pytest.raises(UpdateFailed, match="offline"):
        await coordinator._async_update_data()


@pytest.mark.parametrize(
    "error",
    [MakeraZ1CameraBusyError("busy"), MakeraZ1ConnectionError("offline")],
)
@pytest.mark.asyncio
async def test_camera_still_and_initial_stream_failures(coordinator, error) -> None:
    """Test still and stream startup failures return no image or response."""
    camera = MakeraZ1Camera(coordinator)
    coordinator.client.async_get_camera_image = AsyncMock(side_effect=error)
    assert await camera.async_camera_image() is None

    closed = False

    async def error_frames():
        nonlocal closed
        try:
            raise error
            yield b""  # pragma: no cover
        finally:
            closed = True

    coordinator.client.async_camera_frames = MagicMock(side_effect=error_frames)
    assert await camera.handle_async_mjpeg_stream(MagicMock()) is None
    assert closed


@pytest.mark.asyncio
async def test_camera_prepare_and_stream_write_failures(coordinator) -> None:
    """Test stream resources close when HTTP setup or a viewer write fails."""
    camera = MakeraZ1Camera(coordinator)
    camera.async_write_ha_state = MagicMock()
    closed = False

    async def frames():
        nonlocal closed
        try:
            yield b"first"
            yield b"second"
        finally:
            closed = True

    coordinator.client.async_camera_frames = MagicMock(side_effect=frames)
    response = MagicMock()
    response.headers = {}
    response.prepare = AsyncMock(side_effect=RuntimeError("prepare failed"))
    with (
        patch(
            "custom_components.hakera.camera.web.StreamResponse", return_value=response
        ),
        pytest.raises(RuntimeError, match="prepare failed"),
    ):
        await camera.handle_async_mjpeg_stream(MagicMock())
    assert closed

    closed = False
    response = MagicMock()
    response.headers = {}
    response.prepare = AsyncMock()
    response.write = AsyncMock(side_effect=ConnectionResetError)
    response.write_eof = AsyncMock()
    coordinator.client.async_camera_frames = MagicMock(side_effect=frames)
    with patch(
        "custom_components.hakera.camera.web.StreamResponse", return_value=response
    ):
        result = await camera.handle_async_mjpeg_stream(MagicMock())
    assert result is response
    assert closed
    assert camera.is_streaming is False


@pytest.mark.parametrize(
    "stream_error",
    [MakeraZ1CameraBusyError("busy"), MakeraZ1ResponseError("bad frame")],
)
@pytest.mark.asyncio
async def test_camera_errors_after_first_frame_are_contained(
    coordinator,
    stream_error,
) -> None:
    """Test a live stream ends cleanly when the controller later fails."""
    camera = MakeraZ1Camera(coordinator)
    camera.async_write_ha_state = MagicMock()

    async def frames():
        yield b"first"
        raise stream_error

    coordinator.client.async_camera_frames = MagicMock(side_effect=frames)
    response = MagicMock()
    response.headers = {}
    response.prepare = AsyncMock()
    response.write = AsyncMock()
    response.write_eof = AsyncMock()
    with patch(
        "custom_components.hakera.camera.web.StreamResponse", return_value=response
    ):
        result = await camera.handle_async_mjpeg_stream(MagicMock())
    assert result is response
    assert camera.is_streaming is False
