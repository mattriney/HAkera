# Architecture

The integration is deliberately split into a Home Assistant shell and a small
protocol client.

## Home Assistant layer

- `__init__.py` creates the client and stores runtime objects on
  `ConfigEntry.runtime_data`.
- `config_flow.py` performs UI setup and validates the Z1 by reading the stable
  serial number.
- `coordinator.py` uses `DataUpdateCoordinator` for a single status poll that
  feeds all normal entities.
- `sensor.py` and `binary_sensor.py` expose parsed and automation-focused state.
- `event.py` preserves controller alarm transitions for Home Assistant automations.
- `camera.py` converts the Z1 WebSocket JPEG feed into Home Assistant's direct
  MJPEG camera proxy without claiming ffmpeg/HLS stream support.
- `light.py` exposes the work light, `fan.py` exposes feedback-backed accessory
  outputs, and `select.py` exposes firmware camera frame sizes.
- `diagnostics.py` redacts host and serial identifiers before exporting support
  data.

## Z1 protocol layer

`z1.py` contains the runtime protocol code because HACS requires all files
needed by the integration to live under `custom_components/hakera`.

The confirmed network interfaces are:

- control and status TCP: `<host>:2222`
- camera WebSocket: `ws://<host>:82/ws_video`
- camera start and stop commands: `start_stream` and `stop_stream`
- camera frames: complete JPEG images
- camera resolution: `POST http://<host>/api/camera/resolution`

The normal TCP poll sends only read-only traffic:

- realtime status query byte `0x3f`
- `diagnose`
- `M957`
- identity reads: `sn-get`, `model`, `version`, `ftype`

The only TCP writes outside polling are fixed allowlisted accessory commands:

- work light: `M821` / `M822`
- spindle fan: `M811S<5..100>` / `M812`
- control-box fan: `M801S<5..100>` / `M802`
- external output: `M851S<5..100>` / `M852`

Power values are numeric, range checked, and restricted to 5% steps. Arbitrary
command text cannot enter the command builder. Each action sends `diagnose` and
requires the corresponding firmware state field before it reports success.

## Polling model

Each coordinator refresh opens a short TCP connection, sends the read-only query
batch, parses returned packets, and closes the connection. This avoids holding a
machine-control channel open indefinitely from Home Assistant. Output actions and
polls share a lock so their short TCP sessions cannot overlap.

## Camera model

The Z1 exposes complete JPEG frames through `ws://<host>:82/ws_video`. An
in-process broker owns at most one upstream WebSocket, fans current frames out to
all Home Assistant consumers, and drops stale queued frames for a slow consumer.
The first still or live viewer starts the upstream stream; the final consumer
leaving sends `stop_stream` and closes it. Still requests subscribe to the same
broker, so they do not collide with an active dashboard stream.

The coordinator tracks downstream live viewers separately from broker consumers.
This keeps camera-streaming automations accurate without treating brief still or
resolution requests as live viewing sessions.

`camera.py` overrides `handle_async_mjpeg_stream` to wrap those JPEG frames in a
multipart MJPEG response at Home Assistant's authenticated
`/api/camera_proxy_stream` endpoint. The entity deliberately keeps
`supported_features` at zero: Home Assistant's `STREAM` feature represents an
ffmpeg-readable HLS source or native WebRTC, neither of which the Z1 provides.

Camera resolution changes use the firmware HTTP endpoint while holding a shared
camera subscription. Returned JPEG dimensions confirm the setting. The client
retries once to accommodate firmware that occasionally ignores the first
idempotent request.
