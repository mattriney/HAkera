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
- `camera.py` implements still-image requests without enabling Home Assistant's
  stream feature, because the Z1 camera source is not RTSP or HTTP MJPEG.
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

The Z1 exposes complete JPEG frames through `ws://<host>:82/ws_video`. The
firmware appears to arbitrate a single stream owner, so this integration opens
the WebSocket only inside `async_camera_image`, sends `start_stream`, returns
the first JPEG frame, then sends `stop_stream` and closes the socket.

This is intentionally snapshot-like. A future streaming implementation should
use an internal proxy/broker that tracks Home Assistant viewers and releases the
Z1 WebSocket when the viewer count reaches zero.
