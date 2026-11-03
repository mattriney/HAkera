"""Keep the test camera decoder aligned with the Home Assistant runtime."""

import json
from importlib.metadata import distribution, version

from packaging.requirements import Requirement


def test_camera_decoder_matches_home_assistant_requirement() -> None:
    """Do not silently test a different decoder than Home Assistant installs."""
    manifest_path = distribution("homeassistant").locate_file(
        "homeassistant/components/camera/manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    requirements = [Requirement(value) for value in manifest["requirements"]]
    decoder = next(
        requirement
        for requirement in requirements
        if requirement.name.lower() == "pyturbojpeg"
    )
    assert version("PyTurboJPEG") in decoder.specifier
