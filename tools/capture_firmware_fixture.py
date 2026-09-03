"""Capture a read-only Makera Z1 firmware regression fixture."""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
from datetime import UTC, datetime
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hakera"
sys.path.append(str(COMPONENT))

from z1 import (  # noqa: E402
    MakeraZ1Client,
    MakeraZ1Snapshot,
    parse_controller_info_line,
    parse_spindle_report_line,
)

FIXTURE_SERIAL = "Z1P000000X000001"


def fixture_name(firmware: str) -> str:
    """Return a safe fixture filename for a firmware version."""
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", firmware).strip("_").lower()
    return f"firmware_{normalized or 'unknown'}.json"


def build_fixture(
    snapshot: MakeraZ1Snapshot,
    firmware: str | None,
    *,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a commit-safe regression fixture from one controller snapshot."""
    version = firmware or snapshot.identity.firmware_version or "unknown"
    identity = snapshot.identity.as_diagnostics()
    identity["serial"] = FIXTURE_SERIAL if identity["serial"] else None

    identity_lines: list[str] = []
    spindle_lines: list[str] = []
    for line in snapshot.response_lines:
        identity_field = parse_controller_info_line(line)
        if identity_field:
            key, _ = identity_field
            identity_lines.append(f"sn = {FIXTURE_SERIAL}" if key == "serial" else line)
        if parse_spindle_report_line(line):
            spindle_lines.append(line)

    return {
        "fixture_version": 1,
        "captured_at": (captured_at or datetime.now(UTC)).isoformat(),
        "firmware": version,
        "raw": {
            "status": snapshot.status.raw,
            "diagnostic": snapshot.diagnostic.raw if snapshot.diagnostic else None,
        },
        "expected": {
            "machine_state": snapshot.status.state,
            "identity": identity,
            "identity_lines": identity_lines,
            "spindle_lines": spindle_lines,
            "spindle_report": snapshot.spindle_report.as_diagnostics(),
            "diagnostic_fields": {
                key: {
                    "known": value.known,
                    "value": value.value,
                }
                for key, value in snapshot.diagnostic_fields.items()
            },
        },
    }


async def async_capture(
    host: str,
    firmware: str | None,
    output: pathlib.Path | None,
) -> pathlib.Path:
    """Capture and write one fixture, returning its destination."""
    client = MakeraZ1Client(host)
    try:
        snapshot = await client.async_fetch_snapshot(include_identity=True)
    finally:
        await client.async_close()
    data = build_fixture(snapshot, firmware)

    if output is None:
        output = ROOT / "tests" / "fixtures" / fixture_name(data["firmware"])

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output


def main() -> None:
    """Run the fixture capture command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Makera Z1 host or IPv4 address")
    parser.add_argument("--firmware", help="Firmware version label for the fixture")
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        help="Output fixture path. Defaults to tests/fixtures/firmware_<version>.json",
    )
    args = parser.parse_args()

    output = asyncio.run(async_capture(args.host, args.firmware, args.output))
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
