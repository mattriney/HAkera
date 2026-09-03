"""Tests for Makera Z1 client behavior."""

from __future__ import annotations

import asyncio
import pathlib
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "makera_z1"
sys.path.append(str(COMPONENT))

from z1 import (  # noqa: E402
    ControlPacketParser,
    MakeraZ1CameraBusyError,
    MakeraZ1Client,
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
                    build_control_packet(0x83, b"sn = Z1P012601K012171\n"),
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
        self.assertEqual(snapshot.identity.serial, "Z1P012601K012171")
        self.assertEqual(snapshot.identity.firmware_version, "1.1.2.0.1.13")
        self.assertEqual(snapshot.spindle_report.current_rpm, 0.0)
        self.assertEqual(snapshot.diagnostic_fields["rssi"].value, -63.0)
        self.assertIsNone(snapshot.alert)

    async def test_soft_limit_alert_survives_alarm_lock_until_unlock(self) -> None:
        responses = [
            (
                "Alarm",
                "Soft Endstop X was exceeded - reset or $X or M999 required",
            ),
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
                                "F:0,0|S:0,10000,100|T:1|O:100|H:0|C:1,0>"
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
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual(first.alert.kind, "soft_limit")
        self.assertEqual(first.alert.axis, "X")
        self.assertEqual(second.alert.kind, "soft_limit")
        self.assertEqual(second.alert.axis, "X")
        self.assertIsNone(third.alert)

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


if __name__ == "__main__":
    unittest.main()
