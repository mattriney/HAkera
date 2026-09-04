"""Constants for the Makera Z1 integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "hakera"

MANUFACTURER = "Makera"
MODEL = "Z1"

CONTROL_PORT = 2222
CAMERA_PORT = 82

DEFAULT_SCAN_INTERVAL = timedelta(seconds=5)
SPINDLE_AT_SPEED_TOLERANCE_PERCENT = 5.0

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.EVENT,
    Platform.FAN,
    Platform.LIGHT,
    Platform.SELECT,
    Platform.SENSOR,
]
