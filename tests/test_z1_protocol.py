"""Tests for the Makera Z1 protocol helpers."""

from __future__ import annotations

import pathlib
import sys
import unittest

COMPONENT = (
    pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "makera_z1"
)
sys.path.insert(0, str(COMPONENT))

from z1 import (  # noqa: E402
    ControlPacketParser,
    MachineStreamParser,
    build_control_packet,
    crc16_xmodem,
    map_diagnostic_fields,
    parse_controller_info_line,
    parse_diagnostic_packet,
    parse_spindle_report_line,
    parse_status_packet,
)


class MakeraZ1ProtocolTest(unittest.TestCase):
    """Protocol parity tests from the Node proof-of-concept."""

    def test_crc16_xmodem_standard_check_value(self) -> None:
        self.assertEqual(crc16_xmodem(b"123456789"), 0x31C3)

    def test_build_control_packet(self) -> None:
        packet = build_control_packet(0xA1, b"\x3f")
        self.assertEqual(packet[:5], bytes([0x86, 0x68, 0x00, 0x04, 0xA1]))
        self.assertEqual(packet[5], 0x3F)
        self.assertEqual(packet[-2:], bytes([0x55, 0xAA]))
        self.assertEqual(int.from_bytes(packet[6:8], "big"), crc16_xmodem(packet[2:6]))

    def test_control_parser_fragmented_and_adjacent(self) -> None:
        first = build_control_packet(0xA1, b"?")
        second = build_control_packet(0xA2, b"$H")
        parser = ControlPacketParser()
        self.assertEqual(parser.push(first[:4]), [])
        messages = parser.push(first[4:] + second)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].packet_type, 0xA1)
        self.assertEqual(messages[0].payload, b"?")
        self.assertEqual(messages[1].packet_type, 0xA2)
        self.assertEqual(messages[1].payload, b"$H")

    def test_control_parser_rejects_bad_crc(self) -> None:
        packet = bytearray(build_control_packet(0xA2, b"$X"))
        packet[5] ^= 0x01
        message = ControlPacketParser().push(bytes(packet))[0]
        self.assertEqual(message.kind, "error")
        self.assertIn("CRC mismatch", message.reason or "")

    def test_status_parser_retains_known_and_unknown_fields(self) -> None:
        status = parse_status_packet(
            "<Idle|MPos:1.25,2,-3,4|WPos:0.1,0.2,0.3,0.4|"
            "F:500,0|S:12000,10000,100|A:1|O:100|H:0|C:1,2|X:test>"
        )
        self.assertEqual(status.state, "Idle")
        self.assertEqual(status.machine_position, (1.25, 2.0, -3.0, 4.0))
        self.assertEqual(status.work_position, (0.1, 0.2, 0.3, 0.4))
        self.assertEqual(status.feed, (500.0, 0.0))
        self.assertEqual(status.spindle, (12000.0, 10000.0, 100.0))
        self.assertEqual(status.fields["X"], "test")

    def test_machine_stream_parser(self) -> None:
        parser = MachineStreamParser()
        self.assertEqual(parser.push(b"<Idle|MPos:1,2"), [])
        messages = parser.push(b",3,4>\r\nok\n")
        self.assertEqual(messages[0].kind, "status")
        self.assertEqual(messages[0].value.machine_position, (1.0, 2.0, 3.0, 4.0))
        self.assertEqual(messages[1].kind, "line")
        self.assertEqual(messages[1].value, "ok")

    def test_diagnostic_parser_and_field_map(self) -> None:
        diagnostic = parse_diagnostic_packet(
            "{S:0,10000,0,0,26,23|L:0,0|V:1,31|F:0,0|"
            "G:1,0,1,0,0|T:0|E:0,1,0,0,1,1,0,0|P:1,0|I:0|RSSI:-63}"
        )
        self.assertEqual(diagnostic.values["G"], (1.0, 0.0, 1.0, 0.0, 0.0))
        fields = map_diagnostic_fields(diagnostic)
        self.assertEqual(fields["xPositiveLimit"].value, 1.0)
        self.assertEqual(fields["cover"].value, 1.0)
        self.assertEqual(fields["workLight"].value, 1.0)
        self.assertEqual(fields["probe"].value, 1.0)
        self.assertEqual(fields["toolSetter"].value, 0.0)
        self.assertEqual(fields["spindleTemperature"].value, 26.0)
        self.assertEqual(fields["powerTemperature"].value, 23.0)
        self.assertEqual(fields["rssi"].value, -63.0)

    def test_controller_info_lines(self) -> None:
        self.assertEqual(
            parse_controller_info_line("sn = Z1P012601K012171"),
            ("serial", "Z1P012601K012171"),
        )
        self.assertEqual(
            parse_controller_info_line("model = Z1, 4, 1, 0, Idle"),
            ("model", "Z1, 4, 1, 0, Idle"),
        )
        self.assertEqual(
            parse_controller_info_line("version = 1.1.2.0.1.13"),
            ("firmware_version", "1.1.2.0.1.13"),
        )
        self.assertEqual(
            parse_controller_info_line("ftype = nc"),
            ("filesystem_type", "nc"),
        )
        self.assertEqual(
            parse_controller_info_line("time = 1788318245"),
            ("controller_time", 1788318245),
        )
        self.assertIsNone(parse_controller_info_line("time = not-a-number"))

    def test_spindle_report_lines(self) -> None:
        self.assertEqual(
            parse_spindle_report_line(
                "State: on, Current RPM: 9987 Target RPM: 10000 PWM value: 0.731"
            ),
            {
                "state": "on",
                "current_rpm": 9987.0,
                "target_rpm": 10000.0,
                "pwm_value": 0.731,
            },
        )
        self.assertEqual(
            parse_spindle_report_line(
                "Current RPM: 9991 Analog value: 0.724 Target RPM: 10000"
            ),
            {
                "current_rpm": 9991.0,
                "analog_value": 0.724,
                "target_rpm": 10000.0,
            },
        )
        self.assertEqual(
            parse_spindle_report_line("Current RPM: 0"),
            {"current_rpm": 0.0},
        )


if __name__ == "__main__":
    unittest.main()
