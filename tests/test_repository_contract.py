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
sys.path.append(str(COMPONENT))

from z1 import (  # noqa: E402
    IDENTITY_COMMANDS,
    OUTPUT_CONTROLS,
    POLL_COMMANDS,
    REALTIME_STATUS,
)


class RepositoryContractTest(unittest.TestCase):
    """Validate publishable-repo and constrained-control contracts."""

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

        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], pyproject["project"]["version"])

    def test_hacs_metadata_is_valid(self) -> None:
        hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(hacs["name"], "Makera Z1")
        self.assertIn("sensor", hacs["domains"])
        self.assertIn("binary_sensor", hacs["domains"])
        self.assertIn("camera", hacs["domains"])
        self.assertIn("fan", hacs["domains"])
        self.assertIn("light", hacs["domains"])
        self.assertIn("select", hacs["domains"])

    def test_json_files_are_valid(self) -> None:
        for path in ROOT.rglob("*.json"):
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                json.loads(path.read_text(encoding="utf-8"))

    def test_new_platforms_have_translated_entities(self) -> None:
        strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
        entities = strings["entity"]
        self.assertEqual(
            set(entities["fan"]), {"spindle_fan", "power_fan", "external_output"}
        )
        self.assertIn("work_light", entities["light"])
        self.assertIn("camera_resolution", entities["select"])

    def test_pyproject_is_valid_toml(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["name"], "home-assistant-makera-z1")
        self.assertEqual(data["project"]["requires-python"], ">=3.14.2")

    def test_runtime_command_allowlist_is_constrained(self) -> None:
        self.assertEqual(REALTIME_STATUS, 0x3F)
        self.assertEqual(POLL_COMMANDS, ("diagnose", "M957"))
        self.assertEqual(IDENTITY_COMMANDS, ("sn-get", "model", "version", "ftype"))
        self.assertEqual(
            {
                key: (value.command_on, value.command_off)
                for key, value in OUTPUT_CONTROLS.items()
            },
            {
                "work_light": ("M821", "M822"),
                "spindle_fan": ("M811S{power}", "M812"),
                "power_fan": ("M801S{power}", "M802"),
                "external_output": ("M851S{power}", "M852"),
            },
        )

    def test_no_motion_or_arbitrary_command_platforms_are_exposed(self) -> None:
        forbidden = {
            "button.py",
            "cover.py",
            "number.py",
            "services.yaml",
            "switch.py",
            "text.py",
        }
        present = {path.name for path in COMPONENT.iterdir()}
        self.assertTrue(forbidden.isdisjoint(present))

    def test_camera_initializes_home_assistant_camera_base(self) -> None:
        source = (COMPONENT / "camera.py").read_text(encoding="utf-8")
        self.assertIn("Camera.__init__(self)", source)
        self.assertIn("CameraEntityFeature(0)", source)


if __name__ == "__main__":
    unittest.main()
