"""Tests for the read-only Makera Z1 client behavior."""

from __future__ import annotations

import asyncio
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "makera_z1"
sys.path.insert(0, str(COMPONENT))

from z1 import (  # noqa: E402
    ControlPacketParser,
    MakeraZ1Client,
    build_control_packet,
)


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
                    build_control_packet(0x83, b"State: off, Current RPM:     0  Target RPM: 10000  PWM value: 0.000\n"),
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


if __name__ == "__main__":
    unittest.main()
