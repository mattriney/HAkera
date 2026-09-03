"""Repository-level contract tests for the custom integration."""

from __future__ import annotations

import json
import pathlib
import sys
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT_ROOT = ROOT / "custom_components"
COMPONENT = COMPONENT_ROOT / "makera_z1"
sys.path.insert(0, str(COMPONENT))

from z1 import IDENTITY_COMMANDS, POLL_COMMANDS, REALTIME_STATUS  # noqa: E402


class RepositoryContractTest(unittest.TestCase):
    """Validate publishable-repo and read-only integration contracts."""

    def test_repo_contains_one_hacs_integration(self) -> None:
        integrations = [
            path
            for path in COMPONENT_ROOT.iterdir()
            if path.is_dir() and path.name != "__pycache__"
        ]
        self.assertEqual(integrations, [COMPONENT])

    def test_manifest_matches_home_assistant_basics(self) -> None:
        manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["domain"], "makera_z1")
        self.assertEqual(manifest["name"], "Makera Z1")
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(manifest["integration_type"], "device")
        self.assertEqual(manifest["iot_class"], "local_polling")
        self.assertEqual(manifest["requirements"], [])
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+")

    def test_hacs_metadata_is_valid(self) -> None:
        hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(hacs["name"], "Makera Z1")
        self.assertIn("sensor", hacs["domains"])
        self.assertIn("binary_sensor", hacs["domains"])
        self.assertIn("camera", hacs["domains"])

    def test_json_files_are_valid(self) -> None:
        for path in ROOT.rglob("*.json"):
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                json.loads(path.read_text(encoding="utf-8"))

    def test_pyproject_is_valid_toml(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["name"], "home-assistant-makera-z1")
        self.assertEqual(data["project"]["requires-python"], ">=3.14.2")

    def test_runtime_command_allowlist_is_read_only(self) -> None:
        self.assertEqual(REALTIME_STATUS, 0x3F)
        self.assertEqual(POLL_COMMANDS, ("diagnose", "M957"))
        self.assertEqual(IDENTITY_COMMANDS, ("sn-get", "model", "version", "ftype"))

    def test_no_write_capable_platforms_or_services_are_exposed(self) -> None:
        forbidden = {
            "button.py",
            "cover.py",
            "fan.py",
            "light.py",
            "number.py",
            "select.py",
            "services.yaml",
            "switch.py",
            "text.py",
        }
        present = {path.name for path in COMPONENT.iterdir()}
        self.assertTrue(forbidden.isdisjoint(present))


if __name__ == "__main__":
    unittest.main()
