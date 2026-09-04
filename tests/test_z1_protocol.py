"""Tests for the Makera Z1 protocol helpers."""

from __future__ import annotations

import pathlib
import sys
import unittest
from types import SimpleNamespace

COMPONENT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "hakera"
sys.path.append(str(COMPONENT))

from z1 import (  # noqa: E402
    CAMERA_RESOLUTIONS,
    OUTPUT_CONTROLS,
    ControllerAlert,
    ControlPacketParser,
    DiagnosticField,
    MachineStreamParser,
    OutputControl,
    build_control_packet,
    build_output_command,
    controller_alert_from_halt_code,
    crc16_xmodem,
    diagnostic_switch_is_active,
    is_jpeg,
    jpeg_dimensions,
    map_diagnostic_fields,
    normalize_host,
    parse_controller_alert_line,
    parse_controller_info_line,
    parse_diagnostic_packet,
    parse_finite,
    parse_integer,
    parse_spindle_report_line,
    parse_status_packet,
    sanitize_command,
    snapshot_is_alarmed,
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

    def test_control_parser_recovers_from_garbage_and_malformed_packets(
        self,
    ) -> None:
        valid = build_control_packet(0xA2, b"diagnose")

        parser = ControlPacketParser()
        messages = parser.push(b"garbage" + valid)
        self.assertEqual(messages[0].kind, "discarded")
        self.assertEqual(messages[0].raw, b"garbage")
        self.assertEqual(messages[1].payload, b"diagnose")

        invalid_length = b"\x86\x68\x00\x02\x00\x00"
        messages = ControlPacketParser().push(invalid_length + valid)
        self.assertTrue(
            any(
                message.kind == "error"
                and "Invalid control packet length" in (message.reason or "")
                for message in messages
            )
        )
        self.assertEqual(messages[-1].payload, b"diagnose")

        invalid_trailer = bytearray(valid)
        invalid_trailer[-1] = 0
        message = ControlPacketParser().push(bytes(invalid_trailer))[0]
        self.assertEqual(message.kind, "error")
        self.assertEqual(message.reason, "Invalid control packet trailer.")

        parser = ControlPacketParser()
        self.assertEqual(parser.push(valid[:4]), [])
        parser.reset()
        messages = parser.push(valid)
        self.assertEqual(messages[0].payload, b"diagnose")

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
        self.assertEqual(status.halt_reason_code, 0)
        self.assertEqual(status.fields["X"], "test")

    def test_machine_stream_parser(self) -> None:
        parser = MachineStreamParser()
        self.assertEqual(parser.push(b"<Idle|MPos:1,2"), [])
        messages = parser.push(b",3,4>\r\nok\n")
        self.assertEqual(messages[0].kind, "status")
        self.assertEqual(messages[0].value.machine_position, (1.0, 2.0, 3.0, 4.0))
        self.assertEqual(messages[1].kind, "line")
        self.assertEqual(messages[1].value, "ok")

    def test_machine_stream_parser_flush_and_recovery_paths(self) -> None:
        parser = MachineStreamParser()
        messages = parser.push("notice<Idle|ReadyFlag>")
        self.assertEqual([message.kind for message in messages], ["line", "status"])
        self.assertEqual(messages[0].value, "notice")
        self.assertTrue(messages[1].value.fields["ReadyFlag"])

        self.assertEqual(parser.push("{RSSI:-42}")[0].kind, "diagnostic")
        self.assertEqual(parser.push("final line"), [])
        self.assertEqual(parser.finish_message()[0].value, "final line")

        self.assertEqual(parser.push("<Idle"), [])
        self.assertEqual(parser.finish_message(), [])
        parser.reset()
        self.assertEqual(parser.finish_message(), [])

        oversized = "x" * (64 * 1024 + 1)
        messages = parser.push(oversized)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].kind, "line")
        self.assertEqual(len(messages[0].value), 64 * 1024)

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
        self.assertEqual(fields["spindleFan"].value, 0.0)
        self.assertEqual(fields["spindleFanPower"].value, 0.0)
        self.assertEqual(fields["powerFan"].value, 1.0)
        self.assertEqual(fields["powerFanPower"].value, 31.0)
        self.assertEqual(fields["externalOutput"].value, 0.0)
        self.assertEqual(fields["externalOutputPower"].value, 0.0)

        partial = parse_diagnostic_packet("ignored|:bad|RSSI:nan")
        self.assertNotIn("ignored", partial.fields)
        self.assertFalse(map_diagnostic_fields(partial)["rssi"].known)
        self.assertTrue(
            all(not field.known for field in map_diagnostic_fields(None).values())
        )

    def test_diagnostic_switch_active_low_polarity(self) -> None:
        active = DiagnosticField("Switch", "switch", None, True, 1.0)
        inactive = DiagnosticField("Switch", "switch", None, True, 0.0)
        unknown = DiagnosticField("Switch", "switch", None, False, None)

        self.assertTrue(diagnostic_switch_is_active(active))
        self.assertFalse(diagnostic_switch_is_active(inactive))
        self.assertFalse(diagnostic_switch_is_active(active, active_low=True))
        self.assertTrue(diagnostic_switch_is_active(inactive, active_low=True))
        self.assertIsNone(diagnostic_switch_is_active(unknown, active_low=True))

    def test_controller_info_lines(self) -> None:
        self.assertEqual(
            parse_controller_info_line("sn = Z1P000000X000001"),
            ("serial", "Z1P000000X000001"),
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
        self.assertEqual(
            parse_controller_info_line("sys-time-data = 2026-09-03"),
            ("system_time", "2026-09-03"),
        )
        self.assertIsNone(parse_controller_info_line("serial: unsupported"))
        self.assertIsNone(parse_controller_info_line("sn = "))
        self.assertIsNone(parse_controller_info_line("model = " + "x" * 201))
        self.assertIsNone(parse_controller_info_line("sn = bad\nvalue"))

    def test_controller_alert_lines(self) -> None:
        soft_limit = parse_controller_alert_line(
            "Soft Endstop X was exceeded - reset or $X or M999 required"
        )
        self.assertIsNotNone(soft_limit)
        self.assertEqual(soft_limit.kind, "soft_limit")
        self.assertEqual(soft_limit.axis, "X")
        self.assertIsNone(soft_limit.direction)

        hard_limit = parse_controller_alert_line("ALARM: Hard limit Z+")
        self.assertIsNotNone(hard_limit)
        self.assertEqual(hard_limit.kind, "hard_limit")
        self.assertEqual(hard_limit.axis, "Z")
        self.assertEqual(hard_limit.direction, "positive")

        alarm_lock = parse_controller_alert_line("error:Alarm lock")
        self.assertIsNotNone(alarm_lock)
        self.assertEqual(alarm_lock.kind, "alarm_lock")
        self.assertIsNone(alarm_lock.axis)
        self.assertIsNone(parse_controller_alert_line("error:Unsupported command"))

        cases = {
            "Emergency stop button pressed": ("emergency_stop", None),
            "Y motor alarm": ("motor_alarm", "Y"),
            "Spindle Alarm": ("spindle_alarm", None),
            "ALARM: generic controller fault": ("controller_alarm", None),
            "Entering alarm/halt state": ("controller_alarm", None),
        }
        for message, (kind, axis) in cases.items():
            with self.subTest(message=message):
                alert = parse_controller_alert_line(message)
                self.assertIsNotNone(alert)
                self.assertEqual(alert.kind, kind)
                self.assertEqual(alert.axis, axis)

        negative = parse_controller_alert_line("ALARM: Hard limit A-")
        self.assertIsNotNone(negative)
        self.assertEqual(negative.direction, "negative")
        self.assertIsNone(parse_controller_alert_line(""))

        diagnostics = ControllerAlert(
            message="ALARM: Hard limit X+",
            kind="hard_limit",
            axis="X",
            direction="positive",
            code=21,
        ).as_diagnostics()
        self.assertEqual(diagnostics["axis"], "X")
        self.assertEqual(diagnostics["code"], 21)

    def test_controller_halt_reason_codes(self) -> None:
        expected = {
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

        for code, (message, kind, axis) in expected.items():
            with self.subTest(code=code):
                alert = controller_alert_from_halt_code(code)
                self.assertIsNotNone(alert)
                self.assertEqual(alert.message, message)
                self.assertEqual(alert.kind, kind)
                self.assertEqual(alert.axis, axis)
                self.assertEqual(alert.code, code)

        self.assertIsNone(controller_alert_from_halt_code(None))
        self.assertIsNone(controller_alert_from_halt_code(0))
        unknown = controller_alert_from_halt_code(99)
        self.assertIsNotNone(unknown)
        self.assertEqual(unknown.message, "Controller halt code 99")
        self.assertEqual(unknown.kind, "controller_alarm")
        self.assertEqual(unknown.code, 99)

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
        self.assertIsNone(parse_spindle_report_line(""))
        self.assertIsNone(parse_spindle_report_line("Current RPM: nan"))
        self.assertIsNone(parse_spindle_report_line("x" * 241))
        self.assertIsNone(
            parse_spindle_report_line(
                "State: on\x00, Current RPM: 1 Target RPM: 2 PWM value: 0.5"
            )
        )

    def test_output_commands_are_built_from_fixed_definitions(self) -> None:
        self.assertEqual(
            build_output_command(OUTPUT_CONTROLS["work_light"], True),
            ("M821", None),
        )
        self.assertEqual(
            build_output_command(OUTPUT_CONTROLS["work_light"], False),
            ("M822", None),
        )
        self.assertEqual(
            build_output_command(OUTPUT_CONTROLS["spindle_fan"], True, 35),
            ("M811S35", 35),
        )
        self.assertEqual(
            build_output_command(OUTPUT_CONTROLS["power_fan"], True),
            ("M801S20", 20),
        )
        self.assertEqual(
            build_output_command(OUTPUT_CONTROLS["external_output"], True, 100),
            ("M851S100", 100),
        )
        self.assertEqual(
            build_output_command(OUTPUT_CONTROLS["external_output"], False, 75),
            ("M852", None),
        )

        with self.assertRaisesRegex(ValueError, "increments of 5"):
            build_output_command(OUTPUT_CONTROLS["power_fan"], True, 22)
        with self.assertRaisesRegex(ValueError, "between 5 and 100"):
            build_output_command(OUTPUT_CONTROLS["external_output"], True, 0)
        with self.assertRaisesRegex(ValueError, "does not support power"):
            build_output_command(OUTPUT_CONTROLS["work_light"], True, 50)
        with self.assertRaisesRegex(ValueError, "enabled or disabled"):
            build_output_command(OUTPUT_CONTROLS["work_light"], 1)
        with self.assertRaisesRegex(ValueError, "must be a number"):
            build_output_command(OUTPUT_CONTROLS["power_fan"], True, True)
        with self.assertRaisesRegex(ValueError, "between 5 and 100"):
            build_output_command(OUTPUT_CONTROLS["power_fan"], True, float("inf"))
        incomplete = OutputControl(
            label="test output",
            command_on="M1S{power}",
            command_off="M2",
            state_field="test",
            power_field="testPower",
        )
        with self.assertRaisesRegex(ValueError, "definition is incomplete"):
            build_output_command(incomplete, True)

    def test_host_command_and_packet_validation(self) -> None:
        self.assertEqual(normalize_host(" Z1-PRO.Local "), "z1-pro.local")
        self.assertEqual(normalize_host("192.0.2.10"), "192.0.2.10")
        for host in ("", "192.0.2.10:2222", "2001:db8::1", "bad host!"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                normalize_host(host)

        self.assertEqual(sanitize_command(" diagnose "), b"diagnose")
        for command in ("", "a\nb", "snowman \N{SNOWMAN}", "a\x1fb"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                sanitize_command(command)

        with self.assertRaisesRegex(ValueError, "one byte"):
            build_control_packet(256)
        with self.assertRaisesRegex(ValueError, "too large"):
            build_control_packet(1, bytes(0xFFFD))

    def test_numeric_and_alarm_helpers_handle_invalid_values(self) -> None:
        self.assertIsNone(parse_finite("not-a-number"))
        self.assertIsNone(parse_finite("inf"))
        self.assertIsNone(parse_integer("1.5"))
        self.assertEqual(parse_integer("12"), 12)

        self.assertIsNone(snapshot_is_alarmed(None))
        self.assertFalse(
            snapshot_is_alarmed(
                SimpleNamespace(alert=None, status=SimpleNamespace(state="Idle"))
            )
        )
        self.assertTrue(
            snapshot_is_alarmed(
                SimpleNamespace(alert=None, status=SimpleNamespace(state="Halt"))
            )
        )

    def test_camera_resolution_table_and_jpeg_dimensions(self) -> None:
        self.assertEqual(len(CAMERA_RESOLUTIONS), 15)
        self.assertEqual(
            (CAMERA_RESOLUTIONS[9].value, CAMERA_RESOLUTIONS[9].option),
            (10, "640x480"),
        )
        jpeg = _jpeg_with_dimensions(640, 480)
        self.assertEqual(jpeg_dimensions(jpeg), (640, 480))
        self.assertIsNone(jpeg_dimensions(b"not-a-jpeg"))
        self.assertTrue(is_jpeg(jpeg))
        self.assertFalse(is_jpeg(b"\xff\xd8x"))

        malformed = b"\xff\xd8not-a-marker\xff\xff"
        self.assertIsNone(jpeg_dimensions(malformed))
        self.assertIsNone(jpeg_dimensions(b"\xff\xd8\xff\xda\x00\x02"))
        self.assertIsNone(jpeg_dimensions(b"\xff\xd8\xff\xe0\x00\x01"))
        self.assertIsNone(jpeg_dimensions(b"\xff\xd8\xff\xe0\x00\x10"))
        short_sof = b"\xff\xd8\xff\xc0\x00\x06\x08\x00\x01\x00\x01"
        self.assertIsNone(jpeg_dimensions(short_sof))
        zero_size = _jpeg_with_dimensions(0, 480)
        self.assertIsNone(jpeg_dimensions(zero_size))


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
