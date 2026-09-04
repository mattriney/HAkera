# Changelog

## Unreleased

- Adds automation-focused machine activity, controller-clear, spindle-speed,
  camera-streaming, and controller-event visibility.

## 0.4.4

- Stylizes the public integration name as `HAkera` and updates repository links
  after the GitHub repository was renamed to `mattriney/HAkera`.

## 0.4.3

- Normalizes the firmware's compound model response to `Z1` for Home Assistant
  device metadata while retaining the raw response in diagnostics fixtures.

## 0.4.2

- Uses canonical Home Assistant imports and explicit non-optional runtime data.
- Closes temporary config-flow clients after both successful and failed probes.
- Adds pinned Pyright type checking to the local and GitHub validation suite.

## 0.4.1

- Adds serial-verified host reconfiguration from the Home Assistant UI.
- Makes newly captured firmware fixtures replayable and automatically redacts
  the controller serial before writing them.
- Expands failure-path coverage to 98% and raises the enforced CI floor to 95%.
- Adds guarded, automated GitHub releases and monthly dependency update checks.
- Prevents HACS installation on Home Assistant versions older than the tested
  `2026.8.3` runtime.

## 0.4.0

- Renames the integration, HACS listing, repository package, and component
  domain to `Hakera` / `hakera`. Existing `makera_z1` installations must be
  removed and added again because this is an intentional breaking rename.
- Adds Home Assistant `2026.8.3` config-flow and runtime tests covering setup,
  unload, registry migration, entities, services, camera output, and diagnostics.
- Runs the complete test suite in GitHub Actions with an 80% coverage floor.
- Documents clean integration removal and the reproducible development workflow.
- Adds locally bundled 1x and 2x Home Assistant brand icons required by HACS.

## 0.3.2

- Decodes the controller's persistent `H` halt-reason codes, including
  `H:10` for a soft-limit alarm, without inferring alarms from coordinates.
- Preserves axis details only when the controller explicitly reports them.
- Correctly labels the disabled diagnostic sensor as `Halt reason code`.
- Removes the redundant work-light feedback binary sensor and cleans its old
  entity-registry entry during setup; the feedback-backed light remains.

## 0.3.1

- Adds a dedicated soft-limit alarm binary sensor.
- Parses the firmware's axis-specific soft-endstop, hard-limit, motor, spindle,
  and emergency-stop alarm messages.
- Adds an alarm-reason sensor and reason, type, axis, and direction attributes
  to alarm entities when the controller reports them.
- Retains a specific alarm reason across generic `error:Alarm lock` responses
  and clears it after the controller is unlocked.

## 0.3.0

- Adds feedback-backed work-light control.
- Adds feedback-backed spindle-fan, control-box-fan, and external-output controls
  with percentage power.
- Adds all 15 firmware camera resolution choices and verifies changes from live
  JPEG dimensions.
- Retries one camera resolution request when the firmware does not apply the
  first request.
- Live verified work-light, spindle-fan, control-box-fan, external-output, and
  camera-resolution control against Z1 firmware `1.1.2.0.1.13`.

## 0.2.1

- Fixes camera entity initialization on Home Assistant 2026.8 and later.
- Uses the current Home Assistant camera feature enum.
- Corrects the active-low Z1 lid sensor polarity.

## 0.2.0

- Adds on-demand live MJPEG video through Home Assistant's camera proxy.
- Shares one Z1 camera WebSocket among simultaneous Home Assistant consumers.
- Stops and closes the Z1 camera stream when the final viewer disconnects.
- Serves still requests from the same broker to avoid camera-owner contention.

## 0.1.0

- Initial read-only Makera Z1 family Home Assistant custom integration.
- Adds UI config flow, polling coordinator, sensors, binary sensors, diagnostics,
  on-demand still camera, protocol tests, firmware fixtures, and GitHub
  validation workflow.
