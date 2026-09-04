"""Tests for HAkera release metadata consistency."""

from __future__ import annotations

import json
import pathlib

import pytest

from tools.check_release import validate_release

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_current_release_metadata_matches() -> None:
    """Test the current manifest, package, and changelog versions together."""
    manifest = json.loads(
        (ROOT / "custom_components" / "hakera" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    version = manifest["version"]
    assert validate_release(ROOT, f"v{version}") == version


@pytest.mark.parametrize(
    "tag",
    [
        "0.4.1",
        "v0.4",
        "latest",
        "v1.2.x",
        "v1.2.3-",
        "v1.2.3+",
        "v1.2.3+build+extra",
    ],
)
def test_release_tag_format_is_strict(tag: str) -> None:
    """Test malformed tag names fail before publication."""
    with pytest.raises(ValueError, match="form v1.2.3"):
        validate_release(ROOT, tag)


def test_release_version_mismatch_is_rejected() -> None:
    """Test a valid tag cannot disagree with repository metadata."""
    with pytest.raises(ValueError, match="inconsistent"):
        validate_release(ROOT, "v9.9.9")
