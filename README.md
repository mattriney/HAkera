# Makera Z1 for Home Assistant

Read-only Home Assistant custom integration for monitoring a Makera Z1 / Z1 Pro
over the local network.

This repository is intentionally scoped to monitoring. It does not expose motion,
spindle, tool-change, probing, file upload, arbitrary G-code, or job-control
actions.

## Current status

This is an initial custom-component foundation built from local reverse
engineering and live testing of one Z1 Pro. The TCP status/diagnostics protocol
and camera WebSocket path are implemented, but the integration still needs live
testing inside a real Home Assistant instance before it should be treated as
production-ready.

Confirmed device interfaces used here:

- Control/status TCP: `<z1-host>:2222`
- Camera WebSocket: `ws://<z1-host>:82/ws_video`
- Camera start command: `start_stream`
- Camera stop command: `stop_stream`
- Binary camera frames: complete JPEG images

## Installation

### HACS custom repository

1. Add this repository to HACS as a custom repository of type `Integration`.
2. Install `Makera Z1`.
3. Restart Home Assistant.
4. Go to `Settings -> Devices & services -> Add integration`.
5. Search for `Makera Z1`.
6. Enter the Z1 host or IP address.

### Manual installation

Copy `custom_components/makera_z1` into your Home Assistant configuration
directory:

```text
<ha-config>/custom_components/makera_z1
```

Restart Home Assistant, then add the integration from the UI.

## Entities

The first pass exposes only read-only entities.

Sensors:

- Machine state
- Firmware version
- Filesystem type
- Current tool
- Feed rate
- Feed override
- Spindle current RPM
- Spindle target RPM
- Spindle scale
- Spindle PWM value
- Spindle temperature
- Control-box temperature
- WiFi signal
- Machine and work coordinates, disabled by default
- Homing code, disabled by default until the bit meaning is confirmed

Binary sensors:

- Connected
- Alarm
- Spindle running
- Work light
- Lid
- Emergency stop
- Probe
- Tool setter
- External input
- Positive limit switches, disabled by default

Camera:

- On-demand still camera using the Z1 WebSocket JPEG stream

Home Assistant's normal camera stream support expects an ffmpeg-compatible
source such as RTSP or HTTP MJPEG. The Z1 currently exposes a proprietary
WebSocket JPEG stream instead, so this integration returns still JPEG images on
demand and closes the WebSocket immediately afterward. That avoids holding the
Z1's single camera stream channel when no viewer is asking for an image.

## Known limitations

- No write actions are exposed.
- The camera entity is snapshot/on-demand only; true live streaming will need an
  internal MJPEG proxy or another Home Assistant streaming adapter.
- Studio and Home Assistant can contend for the same Z1 camera stream. If Studio
  already owns the stream, Home Assistant may return no camera image until the
  Studio preview is closed.
- Discovery is not implemented yet. Setup is manual by host/IP.
- The Home Assistant config flow requires the controller to return its serial
  number with `sn-get`; this gives the config entry a stable unique ID.
- Position sensors are disabled by default to avoid noisy state history.

## Development

Run the pure protocol tests without Home Assistant installed:

```powershell
python -m unittest discover -s tests
```

When new Z1 firmware is released, capture a new read-only fixture while the
machine is idle:

```powershell
python tools/capture_firmware_fixture.py --host <z1-ip> --firmware <version>
```

Review the generated JSON under `tests/fixtures/`, then run the tests. This
keeps protocol changes visible in source control before the Home Assistant
integration is updated.

The GitHub workflow also runs:

- HACS validation
- Home Assistant hassfest validation
- Unit tests for the protocol parser/client helpers

## Repository layout

```text
custom_components/makera_z1/  Home Assistant integration
tests/                        Pure Python protocol tests
tests/fixtures/               Firmware protocol regression fixtures
tools/                        Local read-only fixture capture helpers
.github/workflows/            Repository validation
hacs.json                     HACS metadata
```

## Safety posture

The Z1 is a CNC machine. Remote control is intentionally out of scope here.
Future write-capable work should remain in a separate integration or require a
separate explicit safety design, because Home Assistant automations and remote
dashboards are easy places to trigger an unsafe machine action by accident.

## License

No public license has been selected yet. Choose a license before publishing this
as an open-source repository.
