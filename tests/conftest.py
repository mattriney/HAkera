"""Shared fixtures for Home Assistant integration tests."""

from __future__ import annotations

import json
import socket
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from custom_components.hakera.z1 import (
    ControllerIdentity,
    MakeraZ1Snapshot,
    SpindleReport,
    controller_alert_from_halt_code,
    map_diagnostic_fields,
    parse_diagnostic_packet,
    parse_status_packet,
)

pytest_plugins = "pytest_homeassistant_custom_component"


if sys.platform == "win32":
    _real_socket = socket.socket
    _real_socketpair = socket.socketpair

    def _windows_asyncio_socketpair(
        *args: Any, **kwargs: Any
    ) -> tuple[socket.socket, socket.socket]:
        """Let asyncio create its loopback wakeup pair under pytest-socket."""
        guarded_socket = socket.socket
        socket.socket = _real_socket
        try:
            return _real_socketpair(*args, **kwargs)
        finally:
            socket.socket = guarded_socket

    socket.socketpair = _windows_asyncio_socketpair

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "firmware_1_1_2_0_1_13.json"
LOOPBACK_SERVER_TESTS = {
    "test_fetch_snapshot_sends_only_read_only_commands",
    "test_halt_code_identifies_soft_limit_and_preserves_details",
    "test_set_output_rejects_unconfirmed_state",
    "test_set_output_uses_allowlist_and_confirms_feedback",
}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading integrations from this repository."""


@pytest.fixture(autouse=True)
def _allow_explicit_loopback_servers(request: pytest.FixtureRequest) -> None:
    """Allow the protocol tests that intentionally host a fake local Z1."""
    if request.node.name in LOOPBACK_SERVER_TESTS:
        request.getfixturevalue("socket_enabled")


@pytest.fixture
def idle_snapshot() -> MakeraZ1Snapshot:
    """Return a representative snapshot from the live-tested firmware."""
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    raw = fixture["raw"]
    expected = fixture["expected"]
    diagnostic = parse_diagnostic_packet(raw["diagnostic"])
    identity = ControllerIdentity(**expected["identity"])
    spindle_report = SpindleReport(**expected["spindle_report"])
    return MakeraZ1Snapshot(
        status=parse_status_packet(raw["status"]),
        diagnostic=diagnostic,
        diagnostic_fields=map_diagnostic_fields(diagnostic),
        identity=identity,
        spindle_report=spindle_report,
        alert=None,
    )


@pytest.fixture
def soft_limit_snapshot(idle_snapshot: MakeraZ1Snapshot) -> MakeraZ1Snapshot:
    """Return an alarm snapshot using the firmware's explicit halt code."""
    status = parse_status_packet(
        "<Alarm|MPos:42,12,5,0,0|WPos:0,0,0,0,0|F:0,0,100|"
        "S:0,10000,100|T:1|H:10|C:4,1,0,0>"
    )
    return replace(
        idle_snapshot,
        status=status,
        alert=controller_alert_from_halt_code(status.halt_reason_code),
    )
