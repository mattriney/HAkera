"""Tests for the firmware fixture capture helper."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from tools.capture_firmware_fixture import FIXTURE_SERIAL, build_fixture


def test_build_fixture_preserves_replay_lines_and_redacts_serial(
    idle_snapshot,
) -> None:
    """Test that captured protocol lines are replayable and commit-safe."""
    real_serial = "Z1P012345X987654"
    spindle_line = "State: off, Current RPM:     0  Target RPM: 10000  PWM value: 0.000"
    snapshot = replace(
        idle_snapshot,
        identity=replace(idle_snapshot.identity, serial=real_serial),
        response_lines=(
            f"sn = {real_serial}",
            "model = Z1, 4, 1, 0, Idle",
            "version = 1.1.2.0.1.13",
            "ftype = nc",
            spindle_line,
            "ok",
        ),
    )

    payload = build_fixture(
        snapshot,
        None,
        captured_at=datetime(2026, 9, 3, tzinfo=UTC),
    )

    expected = payload["expected"]
    serialized = str(payload)
    assert payload["firmware"] == "1.1.2.0.1.13"
    assert payload["captured_at"] == "2026-09-03T00:00:00+00:00"
    assert expected["identity"]["serial"] == FIXTURE_SERIAL
    assert expected["identity_lines"][0] == f"sn = {FIXTURE_SERIAL}"
    assert expected["spindle_lines"] == [spindle_line]
    assert real_serial not in serialized
    assert "ok" not in expected["identity_lines"]
    assert "ok" not in expected["spindle_lines"]
