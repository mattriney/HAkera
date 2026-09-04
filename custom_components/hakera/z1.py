"""Async Makera Z1 protocol client.

The protocol implementation mirrors the local Node.js proof-of-concept client
that was built from Makera Studio packet captures and live Z1 testing.
"""

from __future__ import annotations

import asyncio
import ipaddress
import math
import re
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Any, Final

CONTROL_PORT: Final = 2222
CAMERA_PORT: Final = 82

PACKET_HEADER: Final = b"\x86\x68"
PACKET_TRAILER: Final = b"\x55\xaa"
PACKET_TYPE_REALTIME: Final = 0xA1
PACKET_TYPE_COMMAND: Final = 0xA2
REALTIME_STATUS: Final = 0x3F

IDENTITY_COMMANDS: Final = ("sn-get", "model", "version", "ftype")
POLL_COMMANDS: Final = ("diagnose", "M957")
WORK_LIGHT_COMMANDS: Final = {False: "M822", True: "M821"}

HOST_RE: Final = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)
NUMBER_RE: Final = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
SOFT_ENDSTOP_RE: Final = re.compile(
    r"soft\s+endstop\s+([xyza])\s+was\s+exceeded", re.IGNORECASE
)
HARD_LIMIT_RE: Final = re.compile(r"hard\s+limit\s+([xyza])([+-])", re.IGNORECASE)


class MakeraZ1Error(Exception):
    """Base Makera Z1 exception."""


class MakeraZ1ConnectionError(MakeraZ1Error):
    """Raised when the Z1 cannot be reached."""


class MakeraZ1ResponseError(MakeraZ1Error):
    """Raised when the Z1 returns an unexpected response."""


class MakeraZ1CameraBusyError(MakeraZ1Error):
    """Raised when the firmware camera channel is occupied."""


@dataclass(frozen=True, slots=True)
class ControllerAlert:
    """One parsed controller alarm or safety message."""

    message: str
    kind: str
    axis: str | None = None
    direction: str | None = None
    code: int | None = None

    def as_diagnostics(self) -> dict[str, str | int | None]:
        """Return diagnostics-safe alert data."""
        return {
            "message": self.message,
            "kind": self.kind,
            "axis": self.axis,
            "direction": self.direction,
            "code": self.code,
        }


HALT_REASON_DETAILS: Final[dict[int, tuple[str, str, str | None]]] = {
    1: ("Halt Manually", "manual_halt", None),
    2: ("Home Fail", "home_failure", None),
    3: ("Probe Fail", "probe_failure", None),
    4: ("Calibrate Fail", "calibration_failure", None),
    5: ("ATC Home Fail", "tool_changer_failure", None),
    6: ("ATC Invalid Tool Number", "tool_changer_failure", None),
    7: ("ATC Drop Tool Fail", "tool_changer_failure", None),
    8: ("ATC Position Occupied", "tool_changer_failure", None),
    9: ("Spindle Overheated", "spindle_overheat", None),
    10: ("Soft Limit Triggered", "soft_limit", None),
    11: ("Cover opened when playing", "cover_open", None),
    12: ("Probe dead or not set", "probe_failure", None),
    13: ("Emergency stop button pressed", "emergency_stop", None),
    14: ("Power Overheated", "control_box_overheat", None),
    15: (
        "Machine has not been homed,Please home first!",
        "not_homed",
        None,
    ),
    21: ("Hard Limit Triggered, reset needed", "hard_limit", None),
    22: ("X Axis Motor Error, reset needed", "motor_alarm", "X"),
    23: ("Y Axis Motor Error, reset needed", "motor_alarm", "Y"),
    24: ("Z Axis Motor Error, reset needed", "motor_alarm", "Z"),
    25: ("Spindle Stall, reset needed", "spindle_stall", None),
    26: ("SD card read fail, reset needed", "storage_error", None),
    41: ("Spindle Alarm, power off/on needed", "spindle_alarm", None),
}


@dataclass(frozen=True, slots=True)
class MachineStatus:
    """Angle-bracketed machine status packet."""

    raw: str
    state: str
    machine_position: tuple[float | None, ...] | None = None
    work_position: tuple[float | None, ...] | None = None
    feed: tuple[float | None, ...] | None = None
    spindle: tuple[float | None, ...] | None = None
    tool: tuple[float | None, ...] | None = None
    accessory: int | None = None
    override: float | None = None
    halt_reason_code: int | None = None
    counters: tuple[int | None, ...] | None = None
    fields: dict[str, str | bool] | None = None

    def as_diagnostics(self) -> dict[str, Any]:
        """Return diagnostics-safe status data."""
        return {
            "raw": self.raw,
            "state": self.state,
            "machine_position": self.machine_position,
            "work_position": self.work_position,
            "feed": self.feed,
            "spindle": self.spindle,
            "tool": self.tool,
            "accessory": self.accessory,
            "override": self.override,
            "halt_reason_code": self.halt_reason_code,
            "counters": self.counters,
            "fields": self.fields,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticStatus:
    """Brace-delimited diagnostic packet."""

    raw: str
    fields: dict[str, str]
    values: dict[str, tuple[float | None, ...]]

    def as_diagnostics(self) -> dict[str, Any]:
        """Return diagnostics-safe diagnostic data."""
        return {
            "raw": self.raw,
            "fields": self.fields,
            "values": self.values,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticField:
    """Mapped Z1 diagnostic field."""

    label: str
    kind: str
    unit: str | None
    known: bool
    value: float | None


def diagnostic_switch_is_active(
    field: DiagnosticField | None, *, active_low: bool = False
) -> bool | None:
    """Translate a mapped firmware switch field into its logical state."""
    if field is None or not field.known or field.value is None:
        return None
    active = field.value != 0
    return not active if active_low else active


@dataclass(frozen=True, slots=True)
class CameraResolution:
    """One firmware-supported live camera frame size."""

    value: int
    width: int
    height: int

    @property
    def option(self) -> str:
        """Return the Home Assistant select option."""
        return f"{self.width}x{self.height}"


CAMERA_RESOLUTIONS: Final = (
    CameraResolution(1, 160, 120),
    CameraResolution(2, 128, 128),
    CameraResolution(3, 176, 144),
    CameraResolution(4, 240, 176),
    CameraResolution(5, 240, 240),
    CameraResolution(6, 320, 240),
    CameraResolution(7, 320, 320),
    CameraResolution(8, 400, 296),
    CameraResolution(9, 480, 320),
    CameraResolution(10, 640, 480),
    CameraResolution(11, 800, 600),
    CameraResolution(12, 1024, 768),
    CameraResolution(13, 1280, 720),
    CameraResolution(14, 1280, 1024),
    CameraResolution(15, 1600, 1200),
)
CAMERA_RESOLUTION_OPTIONS: Final = tuple(
    resolution.option for resolution in CAMERA_RESOLUTIONS
)


@dataclass(frozen=True, slots=True)
class OutputControl:
    """One fixed, feedback-backed Z1 output control."""

    label: str
    command_on: str
    command_off: str
    state_field: str
    power_field: str | None = None
    minimum_power: int | None = None
    maximum_power: int | None = None
    power_step: int | None = None
    default_power: int | None = None


OUTPUT_CONTROLS: Final[dict[str, OutputControl]] = {
    "work_light": OutputControl(
        label="work light",
        command_on=WORK_LIGHT_COMMANDS[True],
        command_off=WORK_LIGHT_COMMANDS[False],
        state_field="workLight",
    ),
    "spindle_fan": OutputControl(
        label="spindle fan",
        command_on="M811S{power}",
        command_off="M812",
        state_field="spindleFan",
        power_field="spindleFanPower",
        minimum_power=5,
        maximum_power=100,
        power_step=5,
        default_power=20,
    ),
    "power_fan": OutputControl(
        label="control-box fan",
        command_on="M801S{power}",
        command_off="M802",
        state_field="powerFan",
        power_field="powerFanPower",
        minimum_power=5,
        maximum_power=100,
        power_step=5,
        default_power=20,
    ),
    "external_output": OutputControl(
        label="external output",
        command_on="M851S{power}",
        command_off="M852",
        state_field="externalOutput",
        power_field="externalOutputPower",
        minimum_power=5,
        maximum_power=100,
        power_step=5,
        default_power=5,
    ),
}


@dataclass(frozen=True, slots=True)
class ControllerIdentity:
    """Read-only controller identity."""

    serial: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    filesystem_type: str | None = None

    def as_diagnostics(self) -> dict[str, Any]:
        """Return diagnostics data; serial is redacted by diagnostics.py."""
        return {
            "serial": self.serial,
            "model": self.model,
            "firmware_version": self.firmware_version,
            "filesystem_type": self.filesystem_type,
        }


@dataclass(frozen=True, slots=True)
class SpindleReport:
    """Read-only spindle report from M957."""

    state: str | None = None
    current_rpm: float | None = None
    target_rpm: float | None = None
    pwm_value: float | None = None
    analog_value: float | None = None

    def as_diagnostics(self) -> dict[str, Any]:
        """Return diagnostics-safe spindle data."""
        return {
            "state": self.state,
            "current_rpm": self.current_rpm,
            "target_rpm": self.target_rpm,
            "pwm_value": self.pwm_value,
            "analog_value": self.analog_value,
        }


@dataclass(frozen=True, slots=True)
class MakeraZ1Snapshot:
    """One read-only Makera Z1 poll result."""

    status: MachineStatus
    diagnostic: DiagnosticStatus | None
    diagnostic_fields: dict[str, DiagnosticField]
    identity: ControllerIdentity
    spindle_report: SpindleReport
    alert: ControllerAlert | None
    response_lines: tuple[str, ...] = ()

    def as_diagnostics(self) -> dict[str, Any]:
        """Return diagnostics-safe snapshot data."""
        return {
            "status": self.status.as_diagnostics(),
            "diagnostic": self.diagnostic.as_diagnostics() if self.diagnostic else None,
            "diagnostic_fields": {
                key: {
                    "label": value.label,
                    "kind": value.kind,
                    "unit": value.unit,
                    "known": value.known,
                    "value": value.value,
                }
                for key, value in self.diagnostic_fields.items()
            },
            "identity": self.identity.as_diagnostics(),
            "spindle_report": self.spindle_report.as_diagnostics(),
            "alert": self.alert.as_diagnostics() if self.alert else None,
        }


@dataclass(frozen=True, slots=True)
class _PacketMessage:
    kind: str
    packet_type: int | None = None
    payload: bytes = b""
    raw: bytes = b""
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _StreamMessage:
    kind: str
    value: Any


@dataclass(frozen=True, slots=True)
class _CameraStreamEvent:
    frame: bytes | None = None
    error: MakeraZ1Error | None = None


@dataclass(frozen=True, slots=True)
class _DiagnosticFieldDefinition:
    label: str
    field: str
    index: int
    kind: str
    unit: str | None = None


Z1_DIAGNOSTIC_FIELDS: Final[dict[str, _DiagnosticFieldDefinition]] = {
    "xPositiveLimit": _DiagnosticFieldDefinition("X+ limit", "E", 1, "switch"),
    "yPositiveLimit": _DiagnosticFieldDefinition("Y+ limit", "E", 3, "switch"),
    "zPositiveLimit": _DiagnosticFieldDefinition("Z+ limit", "E", 4, "switch"),
    "aPositiveLimit": _DiagnosticFieldDefinition("A+ limit", "E", 6, "switch"),
    "probe": _DiagnosticFieldDefinition("Probe", "P", 0, "switch"),
    "toolSetter": _DiagnosticFieldDefinition("Tool setter", "P", 1, "switch"),
    "emergencyStop": _DiagnosticFieldDefinition("E-stop", "I", 0, "switch"),
    "cover": _DiagnosticFieldDefinition("Cover", "E", 5, "switch"),
    "workLight": _DiagnosticFieldDefinition("Work light", "G", 0, "switch"),
    "spindleFan": _DiagnosticFieldDefinition("Spindle fan", "F", 0, "switch"),
    "spindleFanPower": _DiagnosticFieldDefinition(
        "Spindle fan power", "F", 1, "number", "%"
    ),
    "powerFan": _DiagnosticFieldDefinition("Control-box fan", "V", 0, "switch"),
    "powerFanPower": _DiagnosticFieldDefinition(
        "Control-box fan power", "V", 1, "number", "%"
    ),
    "externalOutput": _DiagnosticFieldDefinition("External output", "G", 3, "switch"),
    "externalOutputPower": _DiagnosticFieldDefinition(
        "External output power", "G", 4, "number", "%"
    ),
    "externalInput": _DiagnosticFieldDefinition("External input", "G", 2, "switch"),
    "spindleTemperature": _DiagnosticFieldDefinition(
        "Spindle temp", "S", 4, "number", "C"
    ),
    "powerTemperature": _DiagnosticFieldDefinition("Power temp", "S", 5, "number", "C"),
    "rssi": _DiagnosticFieldDefinition("WiFi signal", "RSSI", 0, "number", "dBm"),
}


class MakeraZ1Client:
    """Small async client for Makera Z1 monitoring and limited outputs."""

    def __init__(
        self,
        host: str,
        *,
        session: Any | None = None,
        control_port: int = CONTROL_PORT,
        camera_port: int = CAMERA_PORT,
        connect_timeout: float = 5.0,
        response_timeout: float = 2.5,
        camera_timeout: float = 6.0,
    ) -> None:
        """Initialize the client."""
        self.host = normalize_host(host)
        self.session = session
        self.control_port = control_port
        self.camera_port = camera_port
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout
        self.camera_timeout = camera_timeout
        self._identity = ControllerIdentity()
        self._spindle_report = SpindleReport()
        self._active_alert: ControllerAlert | None = None
        self._control_lock = asyncio.Lock()
        self._camera_setting_lock = asyncio.Lock()
        self._camera_resolution: CameraResolution | None = None
        self._camera_broker = _CameraStreamBroker(self)

    async def async_fetch_snapshot(
        self,
        *,
        include_identity: bool | None = None,
    ) -> MakeraZ1Snapshot:
        """Fetch one read-only status snapshot from the control socket."""
        async with self._control_lock:
            return await self._async_fetch_snapshot(include_identity=include_identity)

    async def _async_fetch_snapshot(
        self,
        *,
        include_identity: bool | None = None,
    ) -> MakeraZ1Snapshot:
        """Fetch one snapshot while the control lock is held."""
        if include_identity is None:
            include_identity = self._identity.serial is None

        packets = [
            build_control_packet(PACKET_TYPE_REALTIME, bytes([REALTIME_STATUS])),
            *(
                build_control_packet(PACKET_TYPE_COMMAND, sanitize_command(command))
                for command in POLL_COMMANDS
            ),
        ]
        if include_identity:
            packets.extend(
                build_control_packet(PACKET_TYPE_COMMAND, sanitize_command(command))
                for command in IDENTITY_COMMANDS
            )

        packet_parser = ControlPacketParser()
        stream_parser = MachineStreamParser()
        identity = self._identity
        spindle_report = SpindleReport()
        status: MachineStatus | None = None
        diagnostic: DiagnosticStatus | None = None
        observed_alert: ControllerAlert | None = None
        response_lines: list[str] = []

        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.control_port),
                timeout=self.connect_timeout,
            )

            for packet in packets:
                writer.write(packet)
            await writer.drain()

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.response_timeout
            while loop.time() < deadline:
                timeout = max(0.05, deadline - loop.time())
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
                except TimeoutError:
                    break

                if not chunk:
                    break

                for packet_message in packet_parser.push(chunk):
                    if packet_message.kind == "error":
                        raise MakeraZ1ResponseError(
                            packet_message.reason or "Invalid control packet."
                        )
                    if packet_message.kind != "packet":
                        continue

                    messages = [
                        *stream_parser.push(packet_message.payload),
                        *stream_parser.finish_message(),
                    ]
                    for message in messages:
                        if message.kind == "status":
                            status = message.value
                        elif message.kind == "diagnostic":
                            diagnostic = message.value
                        elif message.kind == "line":
                            response_lines.append(message.value)
                            alert = parse_controller_alert_line(message.value)
                            if alert and (
                                observed_alert is None
                                or _alert_priority(alert)
                                > _alert_priority(observed_alert)
                            ):
                                observed_alert = alert
                            info = parse_controller_info_line(message.value)
                            if info:
                                identity = _update_identity(identity, *info)
                                continue
                            spindle = parse_spindle_report_line(message.value)
                            if spindle:
                                spindle_report = _update_spindle(
                                    spindle_report, spindle
                                )

                if (
                    status
                    and diagnostic
                    and (not include_identity or identity.serial)
                    and _spindle_has_data(spindle_report)
                ):
                    break

        except MakeraZ1Error:
            raise
        except (OSError, TimeoutError) as err:
            raise MakeraZ1ConnectionError(
                f"Could not connect to {self.host}:{self.control_port}."
            ) from err
        finally:
            if writer is not None:
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()

        if status is None:
            raise MakeraZ1ResponseError(
                "The controller did not return a status packet."
            )

        self._identity = identity
        self._spindle_report = spindle_report
        controller_is_alarmed = status.state.lower().startswith(("alarm", "halt"))
        status_alert = (
            controller_alert_from_halt_code(status.halt_reason_code)
            if controller_is_alarmed
            else None
        )
        if not controller_is_alarmed:
            self._active_alert = None
        elif status_alert is not None:
            selected_alert = status_alert
            if (
                self._active_alert is not None
                and self._active_alert.kind == status_alert.kind
                and (
                    self._active_alert.code is None
                    or self._active_alert.code == status_alert.code
                )
            ):
                selected_alert = _merge_controller_alerts(
                    self._active_alert, selected_alert
                )
            if observed_alert is not None and observed_alert.kind == status_alert.kind:
                selected_alert = _merge_controller_alerts(
                    observed_alert, selected_alert
                )
            self._active_alert = selected_alert
        elif observed_alert is not None:
            if self._active_alert is None or _alert_priority(
                observed_alert
            ) >= _alert_priority(self._active_alert):
                self._active_alert = observed_alert
        elif self._active_alert is None:
            self._active_alert = ControllerAlert(
                message=status.state,
                kind="controller_alarm",
            )
        return MakeraZ1Snapshot(
            status=status,
            diagnostic=diagnostic,
            diagnostic_fields=map_diagnostic_fields(diagnostic),
            identity=identity,
            spindle_report=spindle_report,
            alert=self._active_alert,
            response_lines=tuple(response_lines),
        )

    @property
    def camera_resolution_option(self) -> str | None:
        """Return the observed or most recently selected camera resolution."""
        return self._camera_resolution.option if self._camera_resolution else None

    async def async_set_work_light(self, enabled: bool) -> DiagnosticStatus:
        """Set the work light and require matching diagnostic feedback."""
        return await self.async_set_output("work_light", enabled)

    async def async_set_output(
        self,
        output_id: str,
        enabled: bool,
        power: int | float | None = None,
    ) -> DiagnosticStatus:
        """Set one allowlisted output and require matching feedback."""
        definition = OUTPUT_CONTROLS.get(output_id)
        if definition is None:
            raise ValueError("Unsupported Z1 output control.")
        command, _ = build_output_command(definition, enabled, power)
        async with self._control_lock:
            return await self._async_set_output(command, definition, enabled)

    async def _async_set_output(
        self,
        command: str,
        definition: OutputControl,
        expected: bool,
    ) -> DiagnosticStatus:
        """Send a fixed output command and verify its diagnostic state."""
        packet = build_control_packet(PACKET_TYPE_COMMAND, sanitize_command(command))
        writer: asyncio.StreamWriter | None = None

        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.control_port),
                timeout=self.connect_timeout,
            )
            writer.write(packet)
            await writer.drain()
        except MakeraZ1Error:
            raise
        except (OSError, TimeoutError) as err:
            raise MakeraZ1ConnectionError(
                f"Could not connect to {self.host}:{self.control_port}."
            ) from err
        finally:
            if writer is not None:
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()

        for delay in (0.15, 0.35, 0.75):
            await asyncio.sleep(delay)
            diagnostic = await self._async_fetch_diagnostic()
            fields = map_diagnostic_fields(diagnostic)
            actual = diagnostic_switch_is_active(fields.get(definition.state_field))
            if actual is expected:
                return diagnostic

        raise MakeraZ1ResponseError(
            f"The controller did not confirm the requested {definition.label} state."
        )

    async def _async_fetch_diagnostic(self) -> DiagnosticStatus:
        """Read one diagnostic packet on a short-lived control connection."""
        packet = build_control_packet(PACKET_TYPE_COMMAND, sanitize_command("diagnose"))
        packet_parser = ControlPacketParser()
        stream_parser = MachineStreamParser()
        writer: asyncio.StreamWriter | None = None

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.control_port),
                timeout=self.connect_timeout,
            )
            writer.write(packet)
            await writer.drain()

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.response_timeout
            while loop.time() < deadline:
                timeout = max(0.05, deadline - loop.time())
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
                except TimeoutError:
                    break
                if not chunk:
                    break

                for packet_message in packet_parser.push(chunk):
                    if packet_message.kind == "error":
                        raise MakeraZ1ResponseError(
                            packet_message.reason or "Invalid control packet."
                        )
                    if packet_message.kind != "packet":
                        continue

                    messages = [
                        *stream_parser.push(packet_message.payload),
                        *stream_parser.finish_message(),
                    ]
                    for message in messages:
                        if message.kind == "diagnostic":
                            return message.value
                        if message.kind == "line" and str(
                            message.value
                        ).lower().startswith(("error", "alarm")):
                            raise MakeraZ1ResponseError(str(message.value))

            raise MakeraZ1ResponseError(
                "The controller did not return output diagnostic feedback."
            )
        except MakeraZ1Error:
            raise
        except (OSError, TimeoutError) as err:
            raise MakeraZ1ConnectionError(
                f"Could not connect to {self.host}:{self.control_port}."
            ) from err
        finally:
            if writer is not None:
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()

    async def async_set_camera_resolution(self, value: int) -> None:
        """Set and verify one firmware camera framesize value."""
        resolution = next(
            (item for item in CAMERA_RESOLUTIONS if item.value == value), None
        )
        if resolution is None:
            raise ValueError("Unsupported camera resolution value.")
        if self.session is None:
            raise MakeraZ1ConnectionError("No HTTP client session is available.")

        async with self._camera_setting_lock:
            for _attempt in range(2):
                frames = self.async_camera_frames()
                try:
                    first_frame = await asyncio.wait_for(
                        anext(frames), timeout=self.camera_timeout
                    )
                    if jpeg_dimensions(first_frame) == (
                        resolution.width,
                        resolution.height,
                    ):
                        self._camera_resolution = resolution
                        return

                    await self._async_post_camera_resolution(resolution)
                    if await self._async_wait_for_camera_resolution(frames, resolution):
                        self._camera_resolution = resolution
                        return
                except (StopAsyncIteration, TimeoutError):
                    pass
                finally:
                    await frames.aclose()

        raise MakeraZ1ResponseError(
            "The camera did not produce the requested frame size after two attempts."
        )

    async def _async_post_camera_resolution(self, resolution: CameraResolution) -> None:
        """Send one idempotent camera framesize request."""
        from aiohttp import ClientError

        session = self.session
        if session is None:
            raise MakeraZ1ConnectionError("No HTTP client session is available.")

        response = None
        url = f"http://{self.host}/api/camera/resolution"
        try:
            response = await asyncio.wait_for(
                session.post(url, json={"resolution": resolution.value}),
                timeout=self.camera_timeout,
            )
            message = (
                await asyncio.wait_for(response.text(), timeout=self.camera_timeout)
            ).strip()
            if not 200 <= response.status < 300 or "failed" in message.lower():
                raise MakeraZ1ResponseError(
                    message
                    or f"Camera firmware rejected the setting ({response.status})."
                )
        except MakeraZ1Error:
            raise
        except (ClientError, OSError, TimeoutError) as err:
            raise MakeraZ1ConnectionError(
                f"Could not set camera resolution through {url}."
            ) from err
        finally:
            if response is not None:
                response.release()

    async def _async_wait_for_camera_resolution(
        self,
        frames: AsyncGenerator[bytes, None],
        resolution: CameraResolution,
    ) -> bool:
        """Wait briefly for a frame that confirms a requested size."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.camera_timeout
        for _frame_number in range(12):
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                frame = await asyncio.wait_for(anext(frames), timeout=remaining)
            except (StopAsyncIteration, TimeoutError):
                return False
            if jpeg_dimensions(frame) == (resolution.width, resolution.height):
                return True
        return False

    def observe_camera_frame(self, frame: bytes) -> None:
        """Cache the framesize represented by a received JPEG."""
        dimensions = jpeg_dimensions(frame)
        if dimensions is None:
            return
        self._camera_resolution = next(
            (
                resolution
                for resolution in CAMERA_RESOLUTIONS
                if (resolution.width, resolution.height) == dimensions
            ),
            None,
        )

    async def async_get_camera_image(self) -> bytes:
        """Return the next JPEG from the shared on-demand camera stream."""
        frames = self.async_camera_frames()
        try:
            return await anext(frames)
        except StopAsyncIteration as err:
            raise MakeraZ1ConnectionError(
                "Camera stream ended without a frame."
            ) from err
        finally:
            await frames.aclose()

    def async_camera_frames(self) -> AsyncGenerator[bytes, None]:
        """Yield live JPEG frames while at least one consumer is subscribed."""
        return self._camera_broker.async_frames()

    async def async_close(self) -> None:
        """Close any persistent resources."""
        await self._camera_broker.async_close()


class _CameraStreamBroker:
    """Share the Z1's single camera channel among local consumers."""

    def __init__(self, client: MakeraZ1Client) -> None:
        """Initialize the broker."""
        self._client = client
        self._lock = asyncio.Lock()
        self._idle = asyncio.Event()
        self._subscribers: set[asyncio.Queue[_CameraStreamEvent]] = set()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def async_frames(self) -> AsyncGenerator[bytes, None]:
        """Subscribe to live frames, dropping stale frames for slow consumers."""
        queue: asyncio.Queue[_CameraStreamEvent] = asyncio.Queue(maxsize=1)
        await self._async_subscribe(queue)
        try:
            while True:
                event = await queue.get()
                if event.error is not None:
                    raise event.error
                if event.frame is None:
                    return
                yield event.frame
        finally:
            await self._async_unsubscribe(queue)

    async def async_close(self) -> None:
        """Stop the upstream stream and release all subscribers."""
        async with self._lock:
            self._closed = True
            subscribers = tuple(self._subscribers)
            self._subscribers.clear()
            self._idle.set()
            task = self._task

        for queue in subscribers:
            self._offer(queue, _CameraStreamEvent())

        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _async_subscribe(self, queue: asyncio.Queue[_CameraStreamEvent]) -> None:
        """Register a consumer and start the upstream stream when needed."""
        async with self._lock:
            if self._closed:
                raise MakeraZ1ConnectionError("Camera client is closed.")
            self._subscribers.add(queue)
            self._idle.clear()
            if self._task is None:
                self._task = asyncio.create_task(
                    self._async_run(), name="hakera_camera_stream"
                )

    async def _async_unsubscribe(
        self, queue: asyncio.Queue[_CameraStreamEvent]
    ) -> None:
        """Remove a consumer and signal the upstream stream when it is idle."""
        async with self._lock:
            self._subscribers.discard(queue)
            if not self._subscribers:
                self._idle.set()

    async def _async_run(self) -> None:
        """Relay one upstream WebSocket connection to all subscribers."""
        from aiohttp import ClientError, WSMsgType

        url = f"ws://{self._client.host}:{self._client.camera_port}/ws_video"
        websocket = None
        current_task = asyncio.current_task()
        try:
            if self._client.session is None:
                raise MakeraZ1ConnectionError("No HTTP client session is available.")

            websocket = await asyncio.wait_for(
                self._client.session.ws_connect(url),
                timeout=self._client.camera_timeout,
            )
            await websocket.send_str("start_stream")

            while True:
                message = await self._async_receive_or_idle(websocket)
                if message is None:
                    break

                if message.type == WSMsgType.BINARY:
                    frame = bytes(message.data)
                    if not is_jpeg(frame):
                        raise MakeraZ1ResponseError("Camera returned a non-JPEG frame.")
                    self._client.observe_camera_frame(frame)
                    await self._async_publish_frame(frame)
                    continue

                if message.type == WSMsgType.TEXT:
                    text = str(message.data)
                    if "occupied" in text.lower():
                        raise MakeraZ1CameraBusyError(text)
                    continue

                if message.type in {
                    WSMsgType.CLOSE,
                    WSMsgType.CLOSED,
                    WSMsgType.CLOSING,
                    WSMsgType.ERROR,
                }:
                    raise MakeraZ1ConnectionError("Camera WebSocket closed.")

        except asyncio.CancelledError:
            raise
        except MakeraZ1Error as err:
            await self._async_terminate(err)
        except (ClientError, OSError, TimeoutError):
            await self._async_terminate(
                MakeraZ1ConnectionError(f"Could not read camera stream from {url}.")
            )
        finally:
            if websocket is not None and not websocket.closed:
                with suppress(Exception):
                    await websocket.send_str("stop_stream")
                with suppress(Exception):
                    await websocket.close()

            async with self._lock:
                if self._task is current_task:
                    self._task = None
                if self._subscribers and not self._closed:
                    self._idle.clear()
                    self._task = asyncio.create_task(
                        self._async_run(), name="hakera_camera_stream"
                    )

    async def _async_receive_or_idle(self, websocket: Any) -> Any | None:
        """Wait for either a camera message or the final viewer to leave."""
        while True:
            receive_task = asyncio.create_task(
                websocket.receive(timeout=self._client.camera_timeout)
            )
            idle_task = asyncio.create_task(self._idle.wait())
            try:
                done, _ = await asyncio.wait(
                    {receive_task, idle_task}, return_when=asyncio.FIRST_COMPLETED
                )
            except BaseException:
                receive_task.cancel()
                idle_task.cancel()
                await asyncio.gather(receive_task, idle_task, return_exceptions=True)
                raise

            if receive_task in done:
                idle_task.cancel()
                with suppress(asyncio.CancelledError):
                    await idle_task
                return receive_task.result()

            receive_task.cancel()
            with suppress(asyncio.CancelledError):
                await receive_task
            async with self._lock:
                if not self._subscribers:
                    return None
                self._idle.clear()

    async def _async_publish_frame(self, frame: bytes) -> None:
        """Publish the newest frame to each active consumer."""
        async with self._lock:
            subscribers = tuple(self._subscribers)
            if not subscribers:
                self._idle.set()

        event = _CameraStreamEvent(frame=frame)
        for queue in subscribers:
            self._offer(queue, event)

    async def _async_terminate(self, error: MakeraZ1Error) -> None:
        """End all current subscriptions with an upstream error."""
        async with self._lock:
            subscribers = tuple(self._subscribers)
            self._subscribers.clear()
            self._idle.set()

        event = _CameraStreamEvent(error=error)
        for queue in subscribers:
            self._offer(queue, event)

    @staticmethod
    def _offer(
        queue: asyncio.Queue[_CameraStreamEvent], event: _CameraStreamEvent
    ) -> None:
        """Put an event without allowing a slow consumer to grow a backlog."""
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(event)


class ControlPacketParser:
    """Parser for framed Makera control packets."""

    def __init__(self) -> None:
        """Initialize the parser."""
        self._buffer = b""

    def push(self, chunk: bytes) -> list[_PacketMessage]:
        """Push bytes into the parser and return complete packet messages."""
        self._buffer += bytes(chunk)
        messages: list[_PacketMessage] = []

        while self._buffer:
            header_index = self._buffer.find(PACKET_HEADER)
            if header_index < 0:
                keep = self._buffer.endswith(PACKET_HEADER[:1])
                discarded = self._buffer[:-1] if keep else self._buffer
                if discarded:
                    messages.append(_PacketMessage(kind="discarded", raw=discarded))
                self._buffer = self._buffer[-1:] if keep else b""
                break

            if header_index > 0:
                messages.append(
                    _PacketMessage(kind="discarded", raw=self._buffer[:header_index])
                )
                self._buffer = self._buffer[header_index:]

            if len(self._buffer) < 6:
                break

            length = int.from_bytes(self._buffer[2:4], "big")
            if length < 3:
                messages.append(
                    _PacketMessage(
                        kind="error",
                        reason=f"Invalid control packet length {length}.",
                    )
                )
                self._buffer = self._buffer[2:]
                continue

            total_length = length + 6
            if len(self._buffer) < total_length:
                break

            packet = self._buffer[:total_length]
            self._buffer = self._buffer[total_length:]

            if packet[-2:] != PACKET_TRAILER:
                messages.append(
                    _PacketMessage(
                        kind="error",
                        reason="Invalid control packet trailer.",
                    )
                )
                continue

            crc_offset = length + 2
            expected_crc = int.from_bytes(packet[crc_offset : crc_offset + 2], "big")
            actual_crc = crc16_xmodem(packet[2:crc_offset])
            if actual_crc != expected_crc:
                messages.append(
                    _PacketMessage(
                        kind="error",
                        reason=(
                            "Control packet CRC mismatch "
                            f"(expected 0x{expected_crc:04x}, "
                            f"calculated 0x{actual_crc:04x})."
                        ),
                    )
                )
                continue

            messages.append(
                _PacketMessage(
                    kind="packet",
                    packet_type=packet[4],
                    payload=packet[5:crc_offset],
                    raw=packet,
                )
            )

        return messages

    def reset(self) -> None:
        """Reset buffered parser state."""
        self._buffer = b""


class MachineStreamParser:
    """Parser for text records inside control packets."""

    def __init__(self) -> None:
        """Initialize the parser."""
        self._buffer = ""

    def push(self, chunk: bytes | str) -> list[_StreamMessage]:
        """Push payload bytes and return parsed stream messages."""
        if isinstance(chunk, bytes):
            self._buffer += chunk.decode("utf-8", "replace")
        else:
            self._buffer += str(chunk)

        messages: list[_StreamMessage] = []

        while self._buffer:
            status_start = self._buffer.find("<")
            diagnostic_start = self._buffer.find("{")
            frame_starts = [
                index for index in (status_start, diagnostic_start) if index >= 0
            ]
            frame_start = min(frame_starts) if frame_starts else -1
            newline_match = re.search(r"[\r\n]", self._buffer)
            newline = newline_match.start() if newline_match else -1

            if frame_start >= 0 and (newline < 0 or frame_start < newline):
                if frame_start > 0:
                    prefix = self._buffer[:frame_start].strip()
                    if prefix:
                        messages.append(_StreamMessage("line", prefix))
                    self._buffer = self._buffer[frame_start:]

                is_diagnostic = self._buffer.startswith("{")
                end_marker = "}" if is_diagnostic else ">"
                frame_end = self._buffer.find(end_marker)
                if frame_end < 0:
                    break

                packet = self._buffer[: frame_end + 1]
                self._buffer = self._buffer[frame_end + 1 :].lstrip("\r\n")
                messages.append(
                    _StreamMessage(
                        "diagnostic" if is_diagnostic else "status",
                        parse_diagnostic_packet(packet)
                        if is_diagnostic
                        else parse_status_packet(packet),
                    )
                )
                continue

            if newline >= 0:
                line = self._buffer[:newline].strip()
                self._buffer = self._buffer[newline + 1 :].lstrip("\n")
                if line:
                    messages.append(_StreamMessage("line", line))
                continue

            if len(self._buffer) > 64 * 1024:
                messages.append(_StreamMessage("line", self._buffer[: 64 * 1024]))
                self._buffer = self._buffer[64 * 1024 :]
            break

        return messages

    def finish_message(self) -> list[_StreamMessage]:
        """Flush a complete final message from the parser."""
        value = self._buffer.strip()
        if (
            not value
            or (value.startswith("<") and not value.endswith(">"))
            or (value.startswith("{") and not value.endswith("}"))
        ):
            return []

        self._buffer = ""
        if value.startswith("{") and value.endswith("}"):
            return [_StreamMessage("diagnostic", parse_diagnostic_packet(value))]
        if value.startswith("<") and value.endswith(">"):
            return [_StreamMessage("status", parse_status_packet(value))]
        return [_StreamMessage("line", value)]

    def reset(self) -> None:
        """Reset buffered parser state."""
        self._buffer = ""


def normalize_host(host: str) -> str:
    """Normalize and validate a Z1 host."""
    normalized = str(host or "").strip()
    if not normalized:
        raise ValueError("Host is required.")
    if ":" in normalized:
        raise ValueError("Enter the Z1 address without a port.")

    with suppress(ValueError):
        address = ipaddress.ip_address(normalized)
        if address.version == 4:
            return normalized
        raise ValueError("Only IPv4 addresses are supported.")

    if not HOST_RE.match(normalized):
        raise ValueError("Host is not a valid IPv4 address or host name.")
    return normalized.lower()


def sanitize_command(command: str) -> bytes:
    """Validate a printable single-line command and return ASCII bytes."""
    value = str(command or "").strip()
    if not value:
        raise ValueError("Machine command is empty.")
    if any(char in value for char in "\r\n\0"):
        raise ValueError("Machine command must contain one line.")
    try:
        data = value.encode("ascii")
    except UnicodeEncodeError as err:
        raise ValueError("Machine command must use printable ASCII.") from err
    if any(byte < 0x20 or byte > 0x7E for byte in data):
        raise ValueError("Machine command must use printable ASCII.")
    return data


def build_output_command(
    definition: OutputControl,
    enabled: bool,
    power: int | float | None = None,
) -> tuple[str, int | None]:
    """Build one command from the fixed output allowlist."""
    if enabled is False:
        return definition.command_off, None
    if enabled is not True:
        raise ValueError("Output state must be enabled or disabled.")

    if definition.power_field is None:
        if power is not None:
            raise ValueError(f"The {definition.label} does not support power control.")
        return definition.command_on, None

    minimum = definition.minimum_power
    maximum = definition.maximum_power
    step = definition.power_step
    amount = definition.default_power if power is None else power
    if minimum is None or maximum is None or step is None or amount is None:
        raise ValueError(f"The {definition.label} power definition is incomplete.")
    if isinstance(amount, bool):
        raise ValueError(f"The {definition.label} power must be a number.")

    numeric = float(amount)
    if not math.isfinite(numeric) or numeric < minimum or numeric > maximum:
        raise ValueError(
            f"The {definition.label} power must be between {minimum} and {maximum}."
        )
    steps = (numeric - minimum) / step
    if not math.isclose(steps, round(steps), abs_tol=1e-9):
        raise ValueError(f"The {definition.label} power must use increments of {step}.")

    normalized = minimum + round(steps) * step
    return definition.command_on.replace("{power}", str(normalized)), normalized


def build_control_packet(packet_type: int, payload: bytes = b"") -> bytes:
    """Build a Makera control packet."""
    if packet_type < 0 or packet_type > 0xFF:
        raise ValueError("Packet type must be one byte.")
    if len(payload) > 0xFFFC:
        raise ValueError("Packet payload is too large.")

    length = len(payload) + 3
    core = length.to_bytes(2, "big") + bytes([packet_type]) + payload
    crc = crc16_xmodem(core)
    return PACKET_HEADER + core + crc.to_bytes(2, "big") + PACKET_TRAILER


def crc16_xmodem(data: bytes) -> int:
    """Return CRC-16/XMODEM for bytes."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def parse_status_packet(packet: str) -> MachineStatus:
    """Parse a Z1 angle-bracketed status packet."""
    raw = str(packet or "").strip()
    content = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
    fields = content.split("|")
    state = fields.pop(0) if fields else "Unknown"
    status_fields: dict[str, str | bool] = {}
    status = MachineStatus(raw=f"<{content}>", state=state, fields=status_fields)

    for field in fields:
        separator = field.find(":")
        if separator < 0:
            status_fields[field] = True
            continue

        key = field[:separator]
        value = field[separator + 1 :]
        status_fields[key] = value

        if key == "MPos":
            status = replace(status, machine_position=parse_number_list(value))
        elif key == "WPos":
            status = replace(status, work_position=parse_number_list(value))
        elif key == "F":
            status = replace(status, feed=parse_number_list(value))
        elif key == "S":
            status = replace(status, spindle=parse_number_list(value))
        elif key == "T":
            status = replace(status, tool=parse_number_list(value))
        elif key == "A":
            status = replace(status, accessory=parse_integer(value))
        elif key == "O":
            status = replace(status, override=parse_finite(value))
        elif key == "H":
            status = replace(status, halt_reason_code=parse_integer(value))
        elif key == "C":
            status = replace(status, counters=parse_integer_list(value))

    return status


def parse_diagnostic_packet(packet: str) -> DiagnosticStatus:
    """Parse a Z1 brace-delimited diagnostic packet."""
    raw = str(packet or "").strip()
    content = raw[1:-1] if raw.startswith("{") and raw.endswith("}") else raw
    fields: dict[str, str] = {}
    values: dict[str, tuple[float | None, ...]] = {}

    for field in content.split("|"):
        separator = field.find(":")
        if separator < 1:
            continue
        key = field[:separator]
        value = field[separator + 1 :]
        fields[key] = value
        values[key] = parse_number_list(value)

    return DiagnosticStatus(raw=f"{{{content}}}", fields=fields, values=values)


def map_diagnostic_fields(
    diagnostic: DiagnosticStatus | None,
) -> dict[str, DiagnosticField]:
    """Map diagnostic packet values to named Z1 fields."""
    mapped: dict[str, DiagnosticField] = {}
    for field_id, definition in Z1_DIAGNOSTIC_FIELDS.items():
        value: float | None = None
        values = diagnostic.values.get(definition.field) if diagnostic else None
        if values and definition.index < len(values):
            candidate = values[definition.index]
            if candidate is not None and math.isfinite(candidate):
                value = candidate
        mapped[field_id] = DiagnosticField(
            label=definition.label,
            kind=definition.kind,
            unit=definition.unit,
            known=value is not None,
            value=value,
        )
    return mapped


def parse_controller_info_line(line: str) -> tuple[str, str | int] | None:
    """Parse controller identity/info lines."""
    raw = str(line or "").strip()
    match = re.match(
        r"^(sn|model|version|ftype|time|sys-time-data)\s*=\s*(.+)$", raw, re.I
    )
    if not match:
        return None

    value = match.group(2).strip()
    if not value or len(value) > 200 or any(char in value for char in "\r\n\0"):
        return None

    field = {
        "sn": "serial",
        "model": "model",
        "version": "firmware_version",
        "ftype": "filesystem_type",
        "time": "controller_time",
        "sys-time-data": "system_time",
    }[match.group(1).lower()]

    if field == "model":
        value = value.partition(",")[0].strip()
        if not value:
            return None

    if field == "controller_time":
        if not re.match(r"^\d{1,16}$", value):
            return None
        return field, int(value)

    return field, value


def parse_controller_alert_line(line: str) -> ControllerAlert | None:
    """Parse a controller alarm line without treating ordinary errors as alarms."""
    message = str(line or "").strip()
    if not message:
        return None
    message = message[:255]

    match = SOFT_ENDSTOP_RE.search(message)
    if match:
        return ControllerAlert(
            message=message,
            kind="soft_limit",
            axis=match.group(1).upper(),
        )

    match = HARD_LIMIT_RE.search(message)
    if match:
        return ControllerAlert(
            message=message,
            kind="hard_limit",
            axis=match.group(1).upper(),
            direction="positive" if match.group(2) == "+" else "negative",
        )

    lowered = message.lower()
    if "emergency stop" in lowered:
        return ControllerAlert(message=message, kind="emergency_stop")

    motor_match = re.search(r"\b([xyza])\s+motor\s+alarm\b", message, re.I)
    if motor_match:
        return ControllerAlert(
            message=message,
            kind="motor_alarm",
            axis=motor_match.group(1).upper(),
        )

    if "spindle alarm" in lowered:
        return ControllerAlert(message=message, kind="spindle_alarm")
    if "alarm lock" in lowered:
        return ControllerAlert(message=message, kind="alarm_lock")
    if lowered.startswith("alarm:") or "entering alarm/halt state" in lowered:
        return ControllerAlert(message=message, kind="controller_alarm")
    return None


def controller_alert_from_halt_code(code: int | None) -> ControllerAlert | None:
    """Decode the persistent controller halt-reason code used by Makera Studio."""
    if code is None or code <= 0:
        return None

    details = HALT_REASON_DETAILS.get(code)
    if details is None:
        return ControllerAlert(
            message=f"Controller halt code {code}",
            kind="controller_alarm",
            code=code,
        )

    message, kind, axis = details
    return ControllerAlert(message=message, kind=kind, axis=axis, code=code)


def snapshot_is_alarmed(snapshot: MakeraZ1Snapshot | None) -> bool | None:
    """Return whether a snapshot reports an active alarm or halt condition."""
    if snapshot is None:
        return None
    return snapshot.alert is not None or snapshot.status.state.lower().startswith(
        ("alarm", "halt")
    )


def _alert_priority(alert: ControllerAlert) -> int:
    """Rank specific alarm causes above the generic alarm-lock response."""
    if alert.kind == "alarm_lock":
        return 0
    if alert.kind == "controller_alarm":
        return 1
    return 2


def _merge_controller_alerts(
    primary: ControllerAlert, fallback: ControllerAlert
) -> ControllerAlert:
    """Preserve details from an event message while attaching its status code."""
    return ControllerAlert(
        message=primary.message,
        kind=primary.kind,
        axis=primary.axis or fallback.axis,
        direction=primary.direction or fallback.direction,
        code=primary.code if primary.code is not None else fallback.code,
    )


def parse_spindle_report_line(line: str) -> dict[str, str | float] | None:
    """Parse read-only spindle diagnostic lines from M957."""
    raw = str(line or "").strip()
    if not raw or len(raw) > 240 or any(char in raw for char in "\r\n\0"):
        return None

    match = re.match(
        rf"^State:\s*([^,]{{1,40}}),\s*Current RPM:\s*{NUMBER_RE}\s+"
        rf"Target RPM:\s*{NUMBER_RE}\s+PWM value:\s*{NUMBER_RE}$",
        raw,
        re.I,
    )
    if match:
        return _spindle_values(
            {
                "state": match.group(1).strip(),
                "current_rpm": match.group(2),
                "target_rpm": match.group(3),
                "pwm_value": match.group(4),
            }
        )

    match = re.match(
        rf"^Current RPM:\s*{NUMBER_RE}\s+Analog value:\s*{NUMBER_RE}\s+"
        rf"Target RPM:\s*{NUMBER_RE}$",
        raw,
        re.I,
    )
    if match:
        return _spindle_values(
            {
                "current_rpm": match.group(1),
                "analog_value": match.group(2),
                "target_rpm": match.group(3),
            }
        )

    match = re.match(rf"^Current RPM:\s*{NUMBER_RE}$", raw, re.I)
    if match:
        return _spindle_values({"current_rpm": match.group(1)})

    return None


def parse_number_list(value: str) -> tuple[float | None, ...]:
    """Parse comma-delimited finite numbers."""
    return tuple(parse_finite(item) for item in value.split(","))


def parse_integer_list(value: str) -> tuple[int | None, ...]:
    """Parse comma-delimited integers."""
    return tuple(parse_integer(item) for item in value.split(","))


def parse_finite(value: str) -> float | None:
    """Parse a finite float."""
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def parse_integer(value: str) -> int | None:
    """Parse an integer."""
    try:
        return int(value, 10)
    except ValueError:
        return None


def is_jpeg(data: bytes) -> bool:
    """Return whether bytes look like a JPEG."""
    return len(data) >= 4 and data[0] == 0xFF and data[1] == 0xD8


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Return JPEG dimensions without decoding the image."""
    if not is_jpeg(data):
        return None

    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return None

        marker = data[offset]
        offset += 1
        if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xDA or offset + 2 > len(data):
            return None

        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            return None
        if marker in start_of_frame:
            if segment_length < 7:
                return None
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return (width, height) if width and height else None
        offset += segment_length

    return None


def _update_identity(
    identity: ControllerIdentity,
    field: str,
    value: str | int,
) -> ControllerIdentity:
    """Return identity with a parsed field applied."""
    if field not in {"serial", "model", "firmware_version", "filesystem_type"}:
        return identity
    return replace(identity, **{field: str(value)})


def _update_spindle(
    spindle_report: SpindleReport,
    values: dict[str, str | float],
) -> SpindleReport:
    """Return spindle report with parsed values applied."""
    return replace(spindle_report, **values)


def _spindle_has_data(spindle_report: SpindleReport) -> bool:
    """Return whether M957 data has been observed."""
    return any(
        value is not None
        for value in (
            spindle_report.state,
            spindle_report.current_rpm,
            spindle_report.target_rpm,
            spindle_report.pwm_value,
            spindle_report.analog_value,
        )
    )


def _spindle_values(raw_values: dict[str, str]) -> dict[str, str | float] | None:
    """Normalize parsed spindle values."""
    values: dict[str, str | float] = {}
    for key, value in raw_values.items():
        if key == "state":
            if not value or not re.match(r"^[\x20-\x7e]+$", value):
                return None
            values[key] = value
            continue
        parsed = parse_finite(value)
        if parsed is None:
            return None
        values[key] = parsed
    return values
