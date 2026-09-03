# Changelog

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

- Initial read-only Makera Z1 / Z1 Pro Home Assistant custom integration.
- Adds UI config flow, polling coordinator, sensors, binary sensors, diagnostics,
  on-demand still camera, protocol tests, firmware fixtures, and GitHub
  validation workflow.
