"""Verify that a Hakera release tag matches all version declarations."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def validate_release(root: pathlib.Path, tag: str) -> str:
    """Validate a release tag and return its normalized version."""
    if not re.fullmatch(
        r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", tag
    ):
        raise ValueError(f"Release tag {tag!r} must use the form v1.2.3.")
    version = tag[1:]

    manifest = json.loads(
        (root / "custom_components" / "hakera" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")

    declared = {
        "manifest.json": manifest.get("version"),
        "pyproject.toml": pyproject.get("project", {}).get("version"),
    }
    mismatches = [
        f"{name} declares {value!r}"
        for name, value in declared.items()
        if value != version
    ]
    if not re.search(rf"^## {re.escape(version)}\s*$", changelog, re.MULTILINE):
        mismatches.append(f"CHANGELOG.md has no {version} release heading")
    if mismatches:
        raise ValueError(
            f"Release tag {tag!r} is inconsistent: " + "; ".join(mismatches)
        )
    return version


def main() -> None:
    """Run the release consistency check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Git tag, for example v0.4.1")
    args = parser.parse_args()
    version = validate_release(ROOT, args.tag)
    print(f"Release metadata agrees on Hakera {version}.")


if __name__ == "__main__":
    main()
