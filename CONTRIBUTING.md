# Contributing

## Scope

The integration permits only fixed work-light, fan, external-output, and camera
resolution commands. Do not add motion, spindle-motor, tool-change, probing, file
upload, arbitrary G-code, or job-control actions to this component.

Any broader machine control needs a separate safety design before it belongs
near Home Assistant.

## Local checks

Install the pinned test dependencies and run the same checks as CI:

```powershell
python -m pip install -r requirements_test.txt
python -m compileall -q custom_components tests tools
python -m ruff check .
python -m ruff format --check .
python -m pyright --pythonpath (python -c "import sys; print(sys.executable)")
python -m pytest --cov=custom_components.hakera --cov-report=term-missing --cov-fail-under=95
```

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
sync. Push a matching `vX.Y.Z` tag only after `main` is green. The release
workflow reruns all validation jobs, verifies those three version declarations,
and creates the GitHub release only if every check passes.
