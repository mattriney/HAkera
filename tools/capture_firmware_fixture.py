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
COMPONENT = ROOT / "custom_components" / "makera_z1"
sys.path.append(str(COMPONENT))

from z1 import MakeraZ1Client  # noqa: E402


def fixture_name(firmware: str) -> str:
    """Return a safe fixture filename for a firmware version."""
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", firmware).strip("_").lower()
    return f"firmware_{normalized or 'unknown'}.json"


async def async_capture(host: str, firmware: str | None, output: pathlib.Path) -> None:
    """Capture and write one fixture."""
    client = MakeraZ1Client(host)
    snapshot = await client.async_fetch_snapshot(include_identity=True)
    version = firmware or snapshot.identity.firmware_version or "unknown"

    data: dict[str, Any] = {
        "fixture_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "firmware": version,
        "raw": {
            "status": snapshot.status.raw,
            "diagnostic": snapshot.diagnostic.raw if snapshot.diagnostic else None,
        },
        "expected": {
            "machine_state": snapshot.status.state,
            "identity": snapshot.identity.as_diagnostics(),
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

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


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

    output = args.output
    if output is None:
        output = ROOT / "tests" / "fixtures" / fixture_name(args.firmware or "unknown")

    asyncio.run(async_capture(args.host, args.firmware, output))
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
