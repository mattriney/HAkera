# Contributing

## Scope

The integration permits only fixed work-light, fan, external-output, and camera
resolution commands. Do not add motion, spindle-motor, tool-change, probing, file
upload, arbitrary G-code, or job-control actions to this component.

Any broader machine control needs a separate safety design before it belongs
near Home Assistant.

## Local checks

Use Python 3.14 on Linux (including WSL); current Home Assistant test dependencies
include Linux-only modules. Install the pinned dependencies and run the CI checks:

```bash
python -m pip install -r requirements_test.txt
python -m compileall -q custom_components tests tools
python -m ruff check .
python -m ruff format --check .
python -m pyright --pythonpath "$(which python)"
python -m pytest --cov=custom_components.hakera --cov-report=term-missing --cov-fail-under=95
```

The official `pytest-homeassistant` package pins Home Assistant `2026.11.0`.
Type checking targets that version's public types. The minimum runtime job uses
`requirements_test_minimum.txt` and the legacy test harness for Home Assistant
`2026.8.3`. Install each harness in its own environment and keep both runtime
checks passing when updating dependencies.

Dependabot excludes the minimum-version requirements file; update that baseline
deliberately when changing the supported minimum. Keep PyTurboJPEG aligned with
Home Assistant's camera requirement; CI checks the installed decoder version.
The Ruff action reads its version from `requirements_test.txt`, matching local
checks.

## Firmware updates

After a new Z1 firmware release, capture a fresh fixture:

```powershell
python tools/capture_firmware_fixture.py --host <z1-ip> --firmware <version>
```

Commit the fixture with any parser/entity changes. The offline tests should make
the protocol delta obvious in review.

## Publishing checklist

Before publishing a release:

- Verify the repository URLs and `codeowners` in `manifest.json`.
- Verify the repository license and documentation.
- Create the first GitHub release so HACS users can pin/install a version.
- Confirm the HACS and hassfest workflows pass.

## Creating a release

Keep the version in `manifest.json`, `pyproject.toml`, and `CHANGELOG.md` in
sync in a release PR. When the manifest change merges into `main`, the release
workflow runs all validation jobs, verifies those three version declarations,
and creates the matching `vX.Y.Z` tag and GitHub release only if every check
passes. Existing releases are skipped on reruns.

Pushing a matching `vX.Y.Z` tag still runs the same release checks. An existing
tag must point to the validated commit before a version-bump run can publish it.
