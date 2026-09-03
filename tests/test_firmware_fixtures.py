"""Regression tests for captured Z1 firmware protocol fixtures."""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hakera"
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.append(str(COMPONENT))

from z1 import (  # noqa: E402
    ControllerIdentity,
    SpindleReport,
    _update_identity,
    _update_spindle,
    map_diagnostic_fields,
    parse_controller_info_line,
    parse_diagnostic_packet,
    parse_spindle_report_line,
    parse_status_packet,
)


class FirmwareFixtureTest(unittest.TestCase):
    """Verify all checked-in firmware fixtures still parse as expected."""

    def test_firmware_fixtures(self) -> None:
        fixtures = sorted(FIXTURES.glob("*.json"))
        self.assertGreater(len(fixtures), 0)

        for fixture_path in fixtures:
            with self.subTest(fixture=fixture_path.name):
                fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
                raw = fixture["raw"]
                expected = fixture["expected"]

                status = parse_status_packet(raw["status"])
                self.assertEqual(status.state, expected["machine_state"])

                diagnostic = parse_diagnostic_packet(raw["diagnostic"])
                fields = map_diagnostic_fields(diagnostic)
                for key, expected_field in expected["diagnostic_fields"].items():
                    if key not in fields:
                        continue
                    self.assertEqual(fields[key].known, expected_field["known"], key)
                    self.assertEqual(fields[key].value, expected_field["value"], key)

                identity = ControllerIdentity()
                for line in expected.get("identity_lines", []):
                    parsed = parse_controller_info_line(line)
                    self.assertIsNotNone(parsed)
                    identity = _update_identity(identity, *parsed)
                self.assertEqual(
                    identity.as_diagnostics(),
                    expected["identity"],
                )

                spindle = SpindleReport()
                for line in expected.get("spindle_lines", []):
                    parsed = parse_spindle_report_line(line)
                    self.assertIsNotNone(parsed)
                    spindle = _update_spindle(spindle, parsed)
                self.assertEqual(
                    spindle.as_diagnostics(),
                    expected["spindle_report"],
                )


if __name__ == "__main__":
    unittest.main()
