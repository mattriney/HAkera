"""Camera platform for Makera Z1."""

from __future__ import annotations

import logging
from contextlib import suppress

from aiohttp import web
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.const import CONTENT_TYPE_MULTIPART
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MakeraZ1ConfigEntry
from .entity import MakeraZ1Entity
from .z1 import (
    MakeraZ1CameraBusyError,
    MakeraZ1ConnectionError,
    MakeraZ1ResponseError,
)

_LOGGER = logging.getLogger(__name__)
_MJPEG_BOUNDARY = "--frameboundary"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakeraZ1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Makera Z1 camera."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities([MakeraZ1Camera(coordinator)])


class MakeraZ1Camera(MakeraZ1Entity, Camera):
    """On-demand Makera Z1 still and live camera."""

    _attr_content_type = "image/jpeg"
    _attr_frame_interval = 1.0
    _attr_supported_features = CameraEntityFeature(0)
    _attr_translation_key = "live_view"

    def __init__(self, coordinator) -> None:
        """Initialize the camera."""
        super().__init__(coordinator, "camera")
        # CoordinatorEntity does not cooperatively initialize Camera in this MRO.
        Camera.__init__(self)

    @property
    def is_on(self) -> bool:
        """Return whether the camera should be considered on."""
        return self.coordinator.last_update_success

    @property
    def is_streaming(self) -> bool:
        """Return whether Home Assistant currently has a live viewer."""
        return self.coordinator.camera_stream_clients > 0

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return one on-demand JPEG image."""
        try:
            return await self.coordinator.client.async_get_camera_image()
        except MakeraZ1CameraBusyError as err:
            _LOGGER.info("Makera Z1 camera stream is busy: %s", err)
        except (MakeraZ1ConnectionError, MakeraZ1ResponseError) as err:
            _LOGGER.debug("Unable to fetch Makera Z1 camera image: %s", err)
        return None

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        """Relay the Z1 WebSocket JPEG feed as an on-demand MJPEG stream."""
        frames = self.coordinator.client.async_camera_frames()
        try:
            first_frame = await anext(frames)
        except MakeraZ1CameraBusyError as err:
            _LOGGER.info("Makera Z1 camera stream is busy: %s", err)
            await frames.aclose()
            return None
        except (MakeraZ1ConnectionError, MakeraZ1ResponseError) as err:
            _LOGGER.debug("Unable to start Makera Z1 camera stream: %s", err)
            await frames.aclose()
            return None

        response = web.StreamResponse()
        response.content_type = CONTENT_TYPE_MULTIPART.format(_MJPEG_BOUNDARY)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"

        try:
            await response.prepare(request)
        except BaseException:
            await frames.aclose()
            raise

        self.coordinator.async_update_camera_stream_clients(1)
        try:
            first_part = _encode_mjpeg_frame(first_frame)
            # Chrome displays the previous MJPEG part, so seed it with two frames.
            await response.write(first_part)
            await response.write(first_part)
            async for frame in frames:
                await response.write(_encode_mjpeg_frame(frame))
        except (ConnectionAbortedError, ConnectionResetError):
            pass
        except MakeraZ1CameraBusyError as err:
            _LOGGER.info("Makera Z1 camera stream became busy: %s", err)
        except (MakeraZ1ConnectionError, MakeraZ1ResponseError) as err:
            _LOGGER.debug("Makera Z1 camera stream ended: %s", err)
        finally:
            await frames.aclose()
            self.coordinator.async_update_camera_stream_clients(-1)
            with suppress(ConnectionError, RuntimeError):
                await response.write_eof()

        return response


def _encode_mjpeg_frame(frame: bytes) -> bytes:
    """Wrap one JPEG in a multipart MJPEG response part."""
    header = (
        f"{_MJPEG_BOUNDARY}\r\n"
        "Content-Type: image/jpeg\r\n"
        f"Content-Length: {len(frame)}\r\n\r\n"
    ).encode("ascii")
    return header + frame + b"\r\n"
