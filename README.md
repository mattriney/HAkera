# HAkera

HAkera is a Home Assistant custom integration for monitoring Makera Z1 CNC
machines and controlling a small set of non-motion accessories over the local
network. It targets the Z1 product family and was initially developed and tested
on the Z1 Pro submodel.

This repository does not expose motion, the spindle motor, tool change, probing,
file upload, arbitrary G-code, or job-control actions.

## Current status

This custom component was built from local reverse engineering and live testing
of one Z1 Pro running firmware `1.1.2.0.1.13`. Its setup, status telemetry, lid
state, spindle telemetry, and on-demand camera have been tested on Home Assistant
`2026.8.3`. Work-light, spindle-fan, and external-output control and feedback, as
well as camera resolution selection, have also been tested on the real machine.
Control-box-fan control and feedback have also been live verified.

Confirmed device interfaces used here:

- Control/status TCP: `<z1-host>:2222`
- Camera WebSocket: `ws://<z1-host>:82/ws_video`
- Camera start command: `start_stream`
- Camera stop command: `stop_stream`
- Binary camera frames: complete JPEG images
- Camera resolution: `POST http://<z1-host>/api/camera/resolution`

## Installation

### HACS custom repository

1. Add this repository to HACS as a custom repository of type `Integration`.
2. Install `HAkera`.
3. Restart Home Assistant.
4. Go to `Settings -> Devices & services -> Add integration`.
5. Search for `HAkera`.
6. Enter the Z1 host or IP address.

### Manual installation

Copy `custom_components/hakera` into your Home Assistant configuration
directory:

```text
<ha-config>/custom_components/hakera
```

Restart Home Assistant, then add the integration from the UI.

### Changing the Z1 address

If DHCP assigns the machine a different address, open the HAkera integration in
`Settings -> Devices & services`, choose `Reconfigure`, and enter the new host.
HAkera verifies the controller serial number before updating and reloading the
entry.

## Removal

1. Go to `Settings -> Devices & services` and open `HAkera`.
2. Open the integration entry menu and select `Delete`.
3. To remove the files as well, uninstall `HAkera` in HACS or delete
   `custom_components/hakera`, then restart Home Assistant.

## Entities

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
- Alarm reason, decoded from the controller's persistent halt-reason code
- Machine and work coordinates, disabled by default
- Halt reason code, disabled by default

Binary sensors:

- Connected
- Alarm
- Soft-limit alarm, with an axis attribute when an axis-specific event is reported
- Spindle running
- Lid
- Emergency stop
- Probe
- Tool setter
- External input
- Positive limit switches, disabled by default

Camera:

- On-demand still images using the Z1 WebSocket JPEG stream
- On-demand live MJPEG video proxied through Home Assistant

Light:

- Work light, with controller-reported state

Fans and powered outputs:

- Spindle fan, with reported power
- Control-box fan, with reported power
- External output (vacuum), with reported power

Each powered output accepts Home Assistant percentages in 5% steps. The entity
continues to show the controller's exact reported value, which can differ from a
manual command while firmware-controlled cooling is active.

Select:

- Camera resolution, covering all 15 firmware frame-size values from `160x120`
  through `1600x1200`

The integration opens the Z1 camera WebSocket only while Home Assistant is
requesting a still or displaying a live view. It converts the proprietary
WebSocket JPEG feed into Home Assistant's authenticated MJPEG camera proxy and
sends `stop_stream` when the last viewer disconnects. Concurrent Home Assistant
viewers share one upstream connection because the Z1 firmware appears to permit
only one camera stream owner.

Changing camera resolution temporarily subscribes to that same on-demand stream,
sends the setting, and verifies the resulting JPEG dimensions. The request is
retried once because firmware `1.1.2.0.1.13` can ignore the first request. Before
the first camera view or selection after a restart, the resolution entity is
`Unknown` because the firmware exposes no current-resolution query.

To keep a dashboard card live while it is visible, select `Live` for its camera
view or use this YAML pattern:

```yaml
type: picture-entity
entity: camera.your_z1_camera
camera_image: camera.your_z1_camera
camera_view: live
show_name: false
show_state: false
```

Replace `camera.your_z1_camera` with the camera entity ID created on your system.

The camera intentionally does not advertise Home Assistant's `STREAM` feature.
That feature means an ffmpeg/HLS or native WebRTC source and also enables
recording services; the Z1 exposes neither. Its direct MJPEG proxy is live video,
but it is not an HLS/WebRTC or recording source.

## Known limitations

- Write actions are limited to the work light, two cooling fans, external output,
  and camera resolution. Motion, spindle-motor, and job controls are not exposed.
- Studio and Home Assistant can contend for the same Z1 camera stream. If Studio
  already owns the stream, Home Assistant may return no camera image until the
  Studio preview is closed.
- Live video is MJPEG without audio. It is intended for viewing in Home Assistant,
  not for `camera.record` or `camera.play_stream`.
- Discovery is not implemented yet. Setup is manual by host/IP.
- The Home Assistant config flow requires the controller to return its serial
  number with `sn-get`; this gives the config entry a stable unique ID.
- Position sensors are disabled by default to avoid noisy state history.
- Alarm reasons are decoded from the persistent `H` halt-reason code even when
  Home Assistant receives only `error:Alarm lock`. The code identifies a soft
  limit but not its axis; an axis is shown only when the controller sends the
  axis-specific event to Home Assistant's connection.

## Development

Install the pinned test dependencies and run the complete Home Assistant and
protocol suite:

```powershell
python -m pip install -r requirements_test.txt
python -m pyright --pythonpath (python -c "import sys; print(sys.executable)")
python -m pytest --cov=custom_components.hakera --cov-report=term-missing --cov-fail-under=95
```

The test dependency release is matched to Home Assistant `2026.8.3`, the same
version used for live verification. Tests exercise the UI config flow, full
config-entry setup and unload, entity registry migration, entity state and
service behavior, diagnostics redaction, camera stills and MJPEG output, plus
the lower-level controller protocol and firmware fixtures. Network access is
blocked by default; the four protocol transport tests explicitly use only a
fake controller bound to `127.0.0.1`.

When new Z1 firmware is released, capture a new read-only fixture while the
machine is idle:

```powershell
python tools/capture_firmware_fixture.py --host <z1-ip> --firmware <version>
```

Review the generated JSON under `tests/fixtures/`, then run the tests. This
keeps protocol changes visible in source control before the Home Assistant
integration is updated.

The GitHub workflow runs:

- HACS validation
- Home Assistant hassfest validation
- Ruff lint and format checks
- Pyright type checking against the pinned Home Assistant runtime
- Home Assistant runtime and protocol tests with a 95% coverage floor

## Repository layout

```text
custom_components/hakera/        Home Assistant integration
custom_components/hakera/brand/  Local Home Assistant brand icons
tests/                        Home Assistant and protocol tests
tests/fixtures/               Firmware protocol regression fixtures
tools/                        Local read-only fixture capture helpers
.github/workflows/            Repository validation
hacs.json                     HACS metadata
```

## Safety posture

The Z1 is a CNC machine. This integration limits writes to lights, cooling or
vacuum-style outputs, and camera configuration. Machine motion, spindle-motor
control, probing, and job control remain out of scope because Home Assistant
automations and remote dashboards can trigger actions without an operator at the
machine.

## License

HAkera is released under the [MIT License](LICENSE).
