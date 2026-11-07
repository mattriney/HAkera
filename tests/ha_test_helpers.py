"""Use the Home Assistant fixtures for the version under test."""

from importlib.util import find_spec

if find_spec("pytest_homeassistant") is None:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry as MockConfigEntry,
    )
else:
    from pytest_homeassistant.common import MockConfigEntry as MockConfigEntry
