# Architecture

The integration is deliberately split into a Home Assistant shell and a small
protocol client.

## Home Assistant layer

- `__init__.py` creates the client and stores runtime objects on
  `ConfigEntry.runtime_data`.
- `config_flow.py` performs UI setup and validates the Z1 by reading the stable
  serial number.
- `coordinator.py` uses `DataUpdateCoordinator` for a single read-only poll that
  feeds all normal entities.
- `sensor.py` and `binary_sensor.py` expose only parsed status and diagnostic
  fields.
- `camera.py` converts the Z1 WebSocket JPEG feed into Home Assistant's direct
  MJPEG camera proxy without claiming ffmpeg/HLS stream support.
- `diagnostics.py` redacts host and serial identifiers before exporting support
  data.

## Z1 protocol layer

`z1.py` contains the runtime protocol code because HACS requires all files
needed by the integration to live under `custom_components/makera_z1`.

The TCP client sends only read-only traffic:

- realtime status query byte `0x3f`
- `diagnose`
- `M957`
- identity reads: `sn-get`, `model`, `version`, `ftype`

No write-capable command builders are included in this repository.

## Polling model

Each coordinator refresh opens a short TCP connection, sends the read-only query
batch, parses returned packets, and closes the connection. This avoids holding a
machine-control channel open indefinitely from Home Assistant. If live testing
shows the Z1 dislikes short polling connections, the next step is a persistent
read-only connection with reconnect/backoff and the same command allowlist.

## Camera model

The Z1 exposes complete JPEG frames through `ws://<host>:82/ws_video`. An
in-process broker owns at most one upstream WebSocket, fans current frames out to
all Home Assistant consumers, and drops stale queued frames for a slow consumer.
The first still or live viewer starts the upstream stream; the final consumer
leaving sends `stop_stream` and closes it. Still requests subscribe to the same
broker, so they do not collide with an active dashboard stream.

`camera.py` overrides `handle_async_mjpeg_stream` to wrap those JPEG frames in a
multipart MJPEG response at Home Assistant's authenticated
`/api/camera_proxy_stream` endpoint. The entity deliberately keeps
`supported_features` at zero: Home Assistant's `STREAM` feature represents an
ffmpeg-readable HLS source or native WebRTC, neither of which the Z1 provides.
