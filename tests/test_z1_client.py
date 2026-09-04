"""Tests for Makera Z1 client behavior."""

from __future__ import annotations

import asyncio
import pathlib
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hakera"
sys.path.append(str(COMPONENT))

from z1 import (  # noqa: E402
    CAMERA_RESOLUTIONS,
    ControllerIdentity,
    ControlPacketParser,
    MakeraZ1CameraBusyError,
    MakeraZ1Client,
    MakeraZ1ConnectionError,
    MakeraZ1ResponseError,
    build_control_packet,
)


class _FakeClientError(Exception):
    """Stand in for aiohttp.ClientError."""


class _FakeWSMsgType:
    """Message constants used by the camera client."""

    BINARY = "binary"
    TEXT = "text"
    CLOSE = "close"
    CLOSED = "closed"
    CLOSING = "closing"
    ERROR = "error"


class _FakeWebSocket:
    """Queue-backed WebSocket used by camera lifecycle tests."""

    def __init__(self) -> None:
        self.messages: asyncio.Queue[SimpleNamespace] = asyncio.Queue()
        self.sent: list[str] = []
        self.closed = False
        self.closed_event = asyncio.Event()

    async def send_str(self, value: str) -> None:
        """Record a text message."""
        self.sent.append(value)

    async def receive(self, *, timeout: float) -> SimpleNamespace:
        """Return the next queued device message."""
        return await self.messages.get()

    async def close(self) -> None:
        """Close the fake WebSocket."""
        self.closed = True
        self.closed_event.set()


class _FakeSession:
    """Create and track fake camera WebSockets."""

    def __init__(self) -> None:
        self.connections: list[_FakeWebSocket] = []
        self.connected = asyncio.Event()
        self.urls: list[str] = []
        self.posts: list[tuple[str, dict[str, int]]] = []
        self.post_frame: bytes | None = None
        self.responses: list[_FakeResponse] = []

    async def ws_connect(self, url: str) -> _FakeWebSocket:
        """Open a fake WebSocket."""
        self.urls.append(url)
        websocket = _FakeWebSocket()
        self.connections.append(websocket)
        self.connected.set()
        return websocket

    async def post(self, url: str, *, json: dict[str, int]) -> _FakeResponse:
        """Record a camera setting request and optionally publish its result."""
        self.posts.append((url, json))
        response = _FakeResponse()
        self.responses.append(response)
        if self.post_frame is not None:
            await self.connections[-1].messages.put(
                SimpleNamespace(type=_FakeWSMsgType.BINARY, data=self.post_frame)
            )
        return response


class _FakeResponse:
    """Minimal aiohttp response used by camera setting tests."""

    def __init__(self, *, status: int = 200, body: str = "success") -> None:
        self.status = status
        self.body = body
        self.released = False

    async def text(self) -> str:
        """Return the fake response body."""
        return self.body

    def release(self) -> None:
        """Record response release."""
        self.released = True


def _fake_aiohttp_module() -> ModuleType:
    """Return the minimal aiohttp surface used by the protocol client."""
    module = ModuleType("aiohttp")
    module.ClientError = _FakeClientError
    module.WSMsgType = _FakeWSMsgType
    return module


async def _wait_until(predicate) -> None:
    """Wait briefly for an async lifecycle side effect."""
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


class MakeraZ1ClientTest(unittest.IsolatedAsyncioTestCase):
    """Async client tests using a local fake controller."""

    async def test_fetch_snapshot_sends_only_read_only_commands(self) -> None:
        received_commands: list[str] = []
        received_realtime: list[int] = []

        async def handle_client(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            data = await reader.read(4096)
            parser = ControlPacketParser()
            for message in parser.push(data):
                if message.packet_type == 0xA1:
                    received_realtime.extend(message.payload)
                elif message.packet_type == 0xA2:
                    received_commands.append(message.payload.decode("ascii"))

            response = b"".join(
                [
                    build_control_packet(
                        0x81,
                        b"<Idle|MPos:-1,-1,-1,0|WPos:0,0,0,0|F:0,0|"
                        b"S:0,10000,100|T:1|O:100|H:0|C:1,0>",
                    ),
                    build_control_packet(
                        0x83,
                        b"{S:0,10000,0,0,26,23|G:0,0,0,0,0|"
                        b"P:0,0|I:0|E:0,0,0,0,0,0,1,0|RSSI:-63}",
                    ),
                    build_control_packet(
                        0x83,
                        b"State: off, Current RPM:     0  "
                        b"Target RPM: 10000  PWM value: 0.000\n",
                    ),
                    build_control_packet(0x83, b"sn = Z1P000000X000001\n"),
                    build_control_packet(0x83, b"model = Z1, 4, 1, 0, Idle\n"),
                    build_control_packet(0x83, b"version = 1.1.2.0.1.13\n"),
                    build_control_packet(0x83, b"ftype = nc\n"),
                ]
            )
            writer.write(response)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            client = MakeraZ1Client("127.0.0.1", control_port=port)
            snapshot = await client.async_fetch_snapshot(include_identity=True)
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual(received_realtime, [0x3F])
        self.assertEqual(
            received_commands,
            ["diagnose", "M957", "sn-get", "model", "version", "ftype"],
        )
        self.assertEqual(snapshot.status.state, "Idle")
        self.assertEqual(snapshot.identity.serial, "Z1P000000X000001")
        self.assertEqual(snapshot.identity.model, "Z1")
        self.assertEqual(snapshot.identity.firmware_version, "1.1.2.0.1.13")
        self.assertEqual(snapshot.spindle_report.current_rpm, 0.0)
        self.assertEqual(snapshot.diagnostic_fields["rssi"].value, -63.0)
        self.assertIsNone(snapshot.alert)
        self.assertIn("sn = Z1P000000X000001", snapshot.response_lines)
        self.assertIn(
            "State: off, Current RPM:     0  Target RPM: 10000  PWM value: 0.000",
            snapshot.response_lines,
        )

    async def test_halt_code_identifies_soft_limit_and_preserves_details(self) -> None:
        responses = [
            ("Alarm", "error:Alarm lock"),
            ("Alarm", "Soft Endstop X was exceeded - reset or $X or M999 required"),
            ("Alarm", "error:Alarm lock"),
            ("Idle", "[Caution: Unlocked]"),
        ]

        async def handle_client(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            await reader.read(4096)
            state, alert = responses.pop(0)
            writer.write(
                b"".join(
                    (
                        build_control_packet(
                            0x81,
                            (
                                f"<{state}|MPos:-201,-1,-1,0|WPos:0,0,0,0|"
                                f"F:0,0|S:0,10000,100|T:1|O:100|"
                                f"H:{10 if state == 'Alarm' else 0}|C:1,0>"
                            ).encode(),
                        ),
                        build_control_packet(
                            0x83,
                            b"{S:0,10000,0,0,26,23|G:0,0,0,0,0|"
                            b"P:0,0|I:0|E:0,0,0,0,0,0,1,0|RSSI:-63}",
                        ),
                        build_control_packet(0x83, f"{alert}\n".encode()),
                        build_control_packet(
                            0x83,
                            b"State: off, Current RPM: 0 Target RPM: 10000 "
                            b"PWM value: 0.000\n",
                        ),
                    )
                )
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            client = MakeraZ1Client("127.0.0.1", control_port=port)
            first = await client.async_fetch_snapshot(include_identity=False)
            second = await client.async_fetch_snapshot(include_identity=False)
            third = await client.async_fetch_snapshot(include_identity=False)
            fourth = await client.async_fetch_snapshot(include_identity=False)
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual(first.alert.kind, "soft_limit")
        self.assertEqual(first.alert.message, "Soft Limit Triggered")
        self.assertEqual(first.alert.code, 10)
        self.assertIsNone(first.alert.axis)
        self.assertEqual(second.alert.kind, "soft_limit")
        self.assertEqual(second.alert.axis, "X")
        self.assertEqual(second.alert.code, 10)
        self.assertEqual(third.alert.axis, "X")
        self.assertEqual(third.alert.code, 10)
        self.assertIsNone(fourth.alert)

    async def test_cached_identity_is_not_requested_on_later_polls(self) -> None:
        response = _snapshot_response()
        reader = AsyncMock()
        reader.read = AsyncMock(return_value=response)
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        client = MakeraZ1Client("127.0.0.1")
        client._identity = ControllerIdentity(serial="Z1P000000X000001")

        with patch(
            "z1.asyncio.open_connection",
            AsyncMock(return_value=(reader, writer)),
        ):
            snapshot = await client.async_fetch_snapshot()

        sent = b"".join(call.args[0] for call in writer.write.call_args_list)
        commands = [
            message.payload.decode("ascii")
            for message in ControlPacketParser().push(sent)
            if message.packet_type == 0xA2
        ]
        self.assertEqual(commands, ["diagnose", "M957"])
        self.assertEqual(snapshot.identity.serial, "Z1P000000X000001")

    async def test_snapshot_reports_connection_and_protocol_failures(self) -> None:
        client = MakeraZ1Client("127.0.0.1")
        with (
            patch(
                "z1.asyncio.open_connection",
                AsyncMock(side_effect=OSError("offline")),
            ),
            self.assertRaises(MakeraZ1ConnectionError),
        ):
            await client.async_fetch_snapshot(include_identity=False)

        reader = AsyncMock()
        reader.read = AsyncMock(return_value=b"")
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        with (
            patch(
                "z1.asyncio.open_connection",
                AsyncMock(return_value=(reader, writer)),
            ),
            self.assertRaisesRegex(MakeraZ1ResponseError, "status packet"),
        ):
            await client.async_fetch_snapshot(include_identity=False)

        bad_packet = bytearray(build_control_packet(0x81, b"<Idle>"))
        bad_packet[5] ^= 1
        reader.read = AsyncMock(return_value=bytes(bad_packet))
        with (
            patch(
                "z1.asyncio.open_connection",
                AsyncMock(return_value=(reader, writer)),
            ),
            self.assertRaisesRegex(MakeraZ1ResponseError, "CRC mismatch"),
        ):
            await client.async_fetch_snapshot(include_identity=False)

    async def test_alarm_without_halt_code_uses_best_reported_reason(self) -> None:
        responses = [
            _snapshot_response(
                state="Alarm", halt_code=0, line="Emergency stop button pressed"
            ),
            _snapshot_response(state="Alarm", halt_code=0, line="error:Alarm lock"),
        ]
        reader = AsyncMock()
        reader.read = AsyncMock(side_effect=responses)
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        client = MakeraZ1Client("127.0.0.1")

        with patch(
            "z1.asyncio.open_connection",
            AsyncMock(return_value=(reader, writer)),
        ):
            first = await client.async_fetch_snapshot(include_identity=False)
            second = await client.async_fetch_snapshot(include_identity=False)

        self.assertEqual(first.alert.kind, "emergency_stop")
        self.assertEqual(second.alert.kind, "emergency_stop")

        fresh = MakeraZ1Client("127.0.0.1")
        reader.read = AsyncMock(
            return_value=_snapshot_response(state="Alarm", halt_code=0)
        )
        with patch(
            "z1.asyncio.open_connection",
            AsyncMock(return_value=(reader, writer)),
        ):
            generic = await fresh.async_fetch_snapshot(include_identity=False)
        self.assertEqual(generic.alert.kind, "controller_alarm")
        self.assertEqual(generic.alert.message, "Alarm")

    async def test_set_output_uses_allowlist_and_confirms_feedback(self) -> None:
        received_commands: list[str] = []

        async def handle_client(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            data = await reader.read(4096)
            parser = ControlPacketParser()
            received_commands.extend(
                message.payload.decode("ascii")
                for message in parser.push(data)
                if message.packet_type == 0xA2
            )
            writer.write(
                build_control_packet(
                    0x83,
                    b"{S:0,10000,0,0,26,23|V:1,35|F:0,0|"
                    b"G:1,0,0,0,0|P:0,0|I:0|E:0,0,0,0,0,0,1,0}",
                )
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            client = MakeraZ1Client("127.0.0.1", control_port=port)
            await client.async_set_output("power_fan", True, 35)
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual(received_commands, ["M801S35", "diagnose"])

    async def test_set_output_rejects_unconfirmed_state(self) -> None:
        async def handle_client(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            await reader.read(4096)
            writer.write(
                build_control_packet(
                    0x83,
                    b"{S:0,10000,0,0,26,23|V:0,0|F:0,0|"
                    b"G:1,0,0,0,0|P:0,0|I:0|E:0,0,0,0,0,0,1,0}",
                )
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            client = MakeraZ1Client("127.0.0.1", control_port=port)
            with self.assertRaisesRegex(MakeraZ1ResponseError, "control-box fan"):
                await client.async_set_output("power_fan", True, 35)
        finally:
            server.close()
            await server.wait_closed()

    async def test_output_validation_and_connection_failures(self) -> None:
        client = MakeraZ1Client("127.0.0.1")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            await client.async_set_output("unknown", True)

        client.async_set_output = AsyncMock(return_value="diagnostic")
        self.assertEqual(await client.async_set_work_light(True), "diagnostic")
        client.async_set_output.assert_awaited_once_with("work_light", True)

        client = MakeraZ1Client("127.0.0.1")
        with (
            patch(
                "z1.asyncio.open_connection",
                AsyncMock(side_effect=OSError("offline")),
            ),
            self.assertRaises(MakeraZ1ConnectionError),
        ):
            await client.async_set_output("power_fan", True, 20)

    async def test_diagnostic_poll_rejects_errors_and_missing_feedback(self) -> None:
        writer = MagicMock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        client = MakeraZ1Client("127.0.0.1")

        for response, error in (
            (build_control_packet(0x83, b"error:Alarm lock\n"), "Alarm lock"),
            (b"", "diagnostic feedback"),
        ):
            reader = AsyncMock()
            reader.read = AsyncMock(return_value=response)
            with (
                patch(
                    "z1.asyncio.open_connection",
                    AsyncMock(return_value=(reader, writer)),
                ),
                self.assertRaisesRegex(MakeraZ1ResponseError, error),
            ):
                await client._async_fetch_diagnostic()

    async def test_camera_image_opens_and_releases_on_demand_stream(self) -> None:
        session = _FakeSession()
        client = MakeraZ1Client("127.0.0.1", session=session)
        self.addAsyncCleanup(client.async_close)
        jpeg = b"\xff\xd8one-frame\xff\xd9"

        with patch.dict(sys.modules, {"aiohttp": _fake_aiohttp_module()}):
            image_task = asyncio.create_task(client.async_get_camera_image())
            await asyncio.wait_for(session.connected.wait(), timeout=1)
            websocket = session.connections[0]
            await _wait_until(lambda: websocket.sent == ["start_stream"])
            await websocket.messages.put(
                SimpleNamespace(type=_FakeWSMsgType.BINARY, data=jpeg)
            )

            self.assertEqual(await asyncio.wait_for(image_task, timeout=1), jpeg)
            await asyncio.wait_for(websocket.closed_event.wait(), timeout=1)

        self.assertEqual(session.urls, ["ws://127.0.0.1:82/ws_video"])
        self.assertEqual(websocket.sent, ["start_stream", "stop_stream"])

    async def test_camera_consumers_share_one_upstream_connection(self) -> None:
        session = _FakeSession()
        client = MakeraZ1Client("127.0.0.1", session=session)
        self.addAsyncCleanup(client.async_close)
        first_jpeg = b"\xff\xd8first\xff\xd9"
        second_jpeg = b"\xff\xd8second\xff\xd9"
        first_frames = client.async_camera_frames()
        second_frames = client.async_camera_frames()

        with patch.dict(sys.modules, {"aiohttp": _fake_aiohttp_module()}):
            first_task = asyncio.create_task(anext(first_frames))
            second_task = asyncio.create_task(anext(second_frames))
            await asyncio.wait_for(session.connected.wait(), timeout=1)
            websocket = session.connections[0]
            await websocket.messages.put(
                SimpleNamespace(type=_FakeWSMsgType.BINARY, data=first_jpeg)
            )

            self.assertEqual(await asyncio.wait_for(first_task, timeout=1), first_jpeg)
            self.assertEqual(await asyncio.wait_for(second_task, timeout=1), first_jpeg)
            self.assertEqual(len(session.connections), 1)

            await first_frames.aclose()
            await asyncio.sleep(0)
            self.assertFalse(websocket.closed)

            second_task = asyncio.create_task(anext(second_frames))
            await websocket.messages.put(
                SimpleNamespace(type=_FakeWSMsgType.BINARY, data=second_jpeg)
            )
            self.assertEqual(
                await asyncio.wait_for(second_task, timeout=1), second_jpeg
            )

            await second_frames.aclose()
            await asyncio.wait_for(websocket.closed_event.wait(), timeout=1)

        self.assertEqual(len(session.connections), 1)
        self.assertEqual(websocket.sent, ["start_stream", "stop_stream"])

    async def test_camera_busy_message_ends_current_subscription(self) -> None:
        session = _FakeSession()
        client = MakeraZ1Client("127.0.0.1", session=session)
        self.addAsyncCleanup(client.async_close)
        frames = client.async_camera_frames()

        with patch.dict(sys.modules, {"aiohttp": _fake_aiohttp_module()}):
            frame_task = asyncio.create_task(anext(frames))
            await asyncio.wait_for(session.connected.wait(), timeout=1)
            websocket = session.connections[0]
            await websocket.messages.put(
                SimpleNamespace(
                    type=_FakeWSMsgType.TEXT,
                    data="The video stream is occupied",
                )
            )

            with self.assertRaises(MakeraZ1CameraBusyError):
                await asyncio.wait_for(frame_task, timeout=1)
            await asyncio.wait_for(websocket.closed_event.wait(), timeout=1)

        self.assertEqual(websocket.sent, ["start_stream", "stop_stream"])

    async def test_camera_stream_rejects_unavailable_and_invalid_sources(self) -> None:
        client = MakeraZ1Client("127.0.0.1")
        frames = client.async_camera_frames()
        with (
            patch.dict(sys.modules, {"aiohttp": _fake_aiohttp_module()}),
            self.assertRaisesRegex(MakeraZ1ConnectionError, "No HTTP client"),
        ):
            await asyncio.wait_for(anext(frames), timeout=1)
        await frames.aclose()

        session = _FakeSession()
        client = MakeraZ1Client("127.0.0.1", session=session)
        self.addAsyncCleanup(client.async_close)
        frames = client.async_camera_frames()
        with patch.dict(sys.modules, {"aiohttp": _fake_aiohttp_module()}):
            frame_task = asyncio.create_task(anext(frames))
            await asyncio.wait_for(session.connected.wait(), timeout=1)
            websocket = session.connections[0]
            await websocket.messages.put(
                SimpleNamespace(type=_FakeWSMsgType.BINARY, data=b"not-jpeg")
            )
            with self.assertRaisesRegex(MakeraZ1ResponseError, "non-JPEG"):
                await asyncio.wait_for(frame_task, timeout=1)
            await asyncio.wait_for(websocket.closed_event.wait(), timeout=1)
        await frames.aclose()

    async def test_camera_stream_ignores_info_and_reports_socket_close(self) -> None:
        session = _FakeSession()
        client = MakeraZ1Client("127.0.0.1", session=session)
        self.addAsyncCleanup(client.async_close)
        frames = client.async_camera_frames()
        jpeg = b"\xff\xd8frame\xff\xd9"

        with patch.dict(sys.modules, {"aiohttp": _fake_aiohttp_module()}):
            frame_task = asyncio.create_task(anext(frames))
            await asyncio.wait_for(session.connected.wait(), timeout=1)
            websocket = session.connections[0]
            await websocket.messages.put(
                SimpleNamespace(type=_FakeWSMsgType.TEXT, data="camera ready")
            )
            await websocket.messages.put(
                SimpleNamespace(type=_FakeWSMsgType.BINARY, data=jpeg)
            )
            self.assertEqual(await asyncio.wait_for(frame_task, timeout=1), jpeg)
            await frames.aclose()
            await asyncio.wait_for(websocket.closed_event.wait(), timeout=1)

        session = _FakeSession()
        client = MakeraZ1Client("127.0.0.1", session=session)
        self.addAsyncCleanup(client.async_close)
        frames = client.async_camera_frames()
        with patch.dict(sys.modules, {"aiohttp": _fake_aiohttp_module()}):
            frame_task = asyncio.create_task(anext(frames))
            await asyncio.wait_for(session.connected.wait(), timeout=1)
            websocket = session.connections[0]
            await websocket.messages.put(
                SimpleNamespace(type=_FakeWSMsgType.CLOSED, data=None)
            )
            with self.assertRaisesRegex(MakeraZ1ConnectionError, "closed"):
                await asyncio.wait_for(frame_task, timeout=1)
        await frames.aclose()

    async def test_camera_client_close_releases_waiters_and_blocks_reuse(self) -> None:
        session = _FakeSession()
        client = MakeraZ1Client("127.0.0.1", session=session)
        frames = client.async_camera_frames()

        with patch.dict(sys.modules, {"aiohttp": _fake_aiohttp_module()}):
            frame_task = asyncio.create_task(anext(frames))
            await asyncio.wait_for(session.connected.wait(), timeout=1)
            await client.async_close()
            with self.assertRaises(StopAsyncIteration):
                await asyncio.wait_for(frame_task, timeout=1)
        await frames.aclose()

        closed_frames = client.async_camera_frames()
        with self.assertRaisesRegex(MakeraZ1ConnectionError, "closed"):
            await anext(closed_frames)
        await closed_frames.aclose()

    async def test_camera_resolution_is_verified_from_stream_dimensions(self) -> None:
        session = _FakeSession()
        session.post_frame = _jpeg_with_dimensions(640, 480)
        client = MakeraZ1Client("127.0.0.1", session=session)
        self.addAsyncCleanup(client.async_close)

        with patch.dict(sys.modules, {"aiohttp": _fake_aiohttp_module()}):
            setting_task = asyncio.create_task(client.async_set_camera_resolution(10))
            await asyncio.wait_for(session.connected.wait(), timeout=1)
            websocket = session.connections[0]
            await websocket.messages.put(
                SimpleNamespace(
                    type=_FakeWSMsgType.BINARY,
                    data=_jpeg_with_dimensions(320, 240),
                )
            )
            await asyncio.wait_for(setting_task, timeout=1)
            await asyncio.wait_for(websocket.closed_event.wait(), timeout=1)

        self.assertEqual(
            session.posts,
            [("http://127.0.0.1/api/camera/resolution", {"resolution": 10})],
        )
        self.assertTrue(session.responses[0].released)
        self.assertEqual(client.camera_resolution_option, "640x480")

    async def test_camera_resolution_shortcuts_and_validation(self) -> None:
        client = MakeraZ1Client("127.0.0.1")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            await client.async_set_camera_resolution(99)
        with self.assertRaisesRegex(MakeraZ1ConnectionError, "No HTTP client"):
            await client.async_set_camera_resolution(10)

        session = _FakeSession()
        client = MakeraZ1Client("127.0.0.1", session=session)
        self.addAsyncCleanup(client.async_close)
        with patch.dict(sys.modules, {"aiohttp": _fake_aiohttp_module()}):
            setting_task = asyncio.create_task(client.async_set_camera_resolution(10))
            await asyncio.wait_for(session.connected.wait(), timeout=1)
            websocket = session.connections[0]
            await websocket.messages.put(
                SimpleNamespace(
                    type=_FakeWSMsgType.BINARY,
                    data=_jpeg_with_dimensions(640, 480),
                )
            )
            await asyncio.wait_for(setting_task, timeout=1)
            await asyncio.wait_for(websocket.closed_event.wait(), timeout=1)
        self.assertEqual(session.posts, [])
        self.assertEqual(client.camera_resolution_option, "640x480")

    async def test_camera_resolution_reports_post_and_stream_failures(self) -> None:
        response = _FakeResponse(status=500, body="failed to apply")
        session = _FakeSession()
        session.post = AsyncMock(return_value=response)
        client = MakeraZ1Client("127.0.0.1", session=session)
        with (
            patch.dict(sys.modules, {"aiohttp": _fake_aiohttp_module()}),
            self.assertRaisesRegex(MakeraZ1ResponseError, "failed to apply"),
        ):
            await client._async_post_camera_resolution(CAMERA_RESOLUTIONS[9])
        self.assertTrue(response.released)

        session.post = AsyncMock(side_effect=_FakeClientError("offline"))
        with (
            patch.dict(sys.modules, {"aiohttp": _fake_aiohttp_module()}),
            self.assertRaises(MakeraZ1ConnectionError),
        ):
            await client._async_post_camera_resolution(CAMERA_RESOLUTIONS[9])

        async def no_frames():
            if False:
                yield b""

        client.async_camera_frames = MagicMock(side_effect=no_frames)
        with self.assertRaisesRegex(MakeraZ1ResponseError, "after two attempts"):
            await client.async_set_camera_resolution(10)

        with self.assertRaisesRegex(MakeraZ1ConnectionError, "without a frame"):
            await client.async_get_camera_image()

    async def test_camera_resolution_wait_and_observation_edges(self) -> None:
        session = _FakeSession()
        client = MakeraZ1Client("127.0.0.1", session=session, camera_timeout=0)

        async def one_frame():
            yield _jpeg_with_dimensions(320, 240)

        self.assertFalse(
            await client._async_wait_for_camera_resolution(
                one_frame(), CAMERA_RESOLUTIONS[9]
            )
        )

        client.camera_timeout = 1
        self.assertFalse(
            await client._async_wait_for_camera_resolution(
                one_frame(), CAMERA_RESOLUTIONS[9]
            )
        )
        client.observe_camera_frame(b"not-jpeg")
        self.assertIsNone(client.camera_resolution_option)


def _jpeg_with_dimensions(width: int, height: int) -> bytes:
    """Build a minimal JPEG header with a baseline SOF segment."""
    return b"".join(
        (
            b"\xff\xd8\xff\xc0\x00\x11\x08",
            height.to_bytes(2, "big"),
            width.to_bytes(2, "big"),
            b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9",
        )
    )


def _snapshot_response(
    *,
    state: str = "Idle",
    halt_code: int = 0,
    line: str | None = None,
) -> bytes:
    """Build one complete read-only controller response."""
    packets = [
        build_control_packet(
            0x81,
            (
                f"<{state}|MPos:-1,-1,-1,0|WPos:0,0,0,0|F:0,0|"
                f"S:0,10000,100|T:1|O:100|H:{halt_code}|C:1,0>"
            ).encode(),
        ),
        build_control_packet(
            0x83,
            b"{S:0,10000,0,0,26,23|G:0,0,0,0,0|P:0,0|I:0|E:0,0,0,0,0,0,1,0|RSSI:-63}",
        ),
        build_control_packet(
            0x83,
            b"State: off, Current RPM: 0 Target RPM: 10000 PWM value: 0.000\n",
        ),
    ]
    if line is not None:
        packets.append(build_control_packet(0x83, f"{line}\n".encode()))
    return b"".join(packets)


if __name__ == "__main__":
    unittest.main()
