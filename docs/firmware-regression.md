# Firmware Regression Testing

The Z1 firmware can change the shape of status packets, diagnostic fields, and
line-oriented command replies. This repo treats those raw records as fixtures so
parser changes are reviewed instead of guessed.

## Capture after a firmware update

1. Update the Z1 firmware.
2. Confirm the machine is idle.
3. Close Makera Studio's camera preview so it does not own the camera channel.
4. Run:

   ```powershell
   python tools/capture_firmware_fixture.py --host <z1-ip> --firmware <version>
   ```

5. Inspect the generated file in `tests/fixtures/`.
6. Run:

   ```powershell
   python -m unittest discover -s tests
   ```

7. Commit the fixture with any required parser/entity changes.

## What gets captured

The helper records only read-only protocol data:

- raw status packet
- raw diagnostic packet
- controller identity values
- spindle status values
- mapped diagnostic fields

It does not capture WiFi credentials, file contents, camera frames, G-code, or
machine-control commands.

## How CI uses it

`tests/test_firmware_fixtures.py` loads every JSON fixture and verifies that the
parser still returns the expected machine state, identity, diagnostic fields,
and spindle values. When a future firmware release changes fields, the fixture
test should fail until the integration is updated deliberately.
