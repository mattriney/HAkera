"""Constants for the Makera Z1 integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "makera_z1"

MANUFACTURER = "Makera"
MODEL = "Z1"

CONTROL_PORT = 2222
CAMERA_PORT = 82

DEFAULT_SCAN_INTERVAL = timedelta(seconds=5)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.SENSOR,
]
