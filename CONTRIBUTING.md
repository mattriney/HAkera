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
python -m pytest --cov=custom_components.hakera --cov-report=term-missing --cov-fail-under=80
```

## Firmware updates

After a new Z1 firmware release, capture a fresh fixture:

```powershell
python tools/capture_firmware_fixture.py --host <z1-ip> --firmware <version>
```

Commit the fixture with any parser/entity changes. The offline tests should make
the protocol delta obvious in review.

## Publishing checklist

Before publishing to GitHub:

- Replace placeholder GitHub URLs in `manifest.json`.
- Add real `codeowners` in `manifest.json` if desired.
- Choose and add a license.
- Create the first GitHub release so HACS users can pin/install a version.
- Confirm the HACS and hassfest workflows pass.
