"""Media player: the Doppler's speaker, for the open-firmware bridge.

Anything Home Assistant can hand out as a URL plays on the clock: `tts.speak`
with this player as the target, `media_player.play_media` with a stream or
media-source item (radio, podcasts, local media), the media browser. The
state also shows a phone playing over Bluetooth.
"""

from __future__ import annotations

import logging
from typing import Any

from doppyler.model.doppler import Doppler

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityDescription,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DopplerDataUpdateCoordinator
from .const import DOMAIN
from .entity import DopplerBridgeEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_devices: AddEntitiesCallback
) -> None:
    @callback
    def async_add_device(device: Doppler) -> None:
        coordinator: DopplerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
            device.dsn
        ]
        async_add_devices(
            [
                DopplerMediaPlayer(
                    coordinator,
                    entry,
                    device,
                    MediaPlayerEntityDescription(
                        "Speaker",
                        name="Speaker",
                        device_class=MediaPlayerDeviceClass.SPEAKER,
                    ),
                )
            ]
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{DOMAIN}_{entry.entry_id}_device_added", async_add_device
        )
    )


class DopplerMediaPlayer(
    DopplerBridgeEntity[MediaPlayerEntityDescription], MediaPlayerEntity
):
    """The clock's speaker."""

    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )
    _attr_media_content_type = MediaType.MUSIC

    @property
    def _audio(self) -> dict:
        audio = self.bridge_get("audio")
        return audio if isinstance(audio, dict) else {}

    @property
    def _bluetooth(self) -> dict:
        bt = self.bridge_get("bluetooth")
        return bt if isinstance(bt, dict) else {}

    @property
    def state(self) -> MediaPlayerState | None:
        if self.bridge_data is None:
            return None
        if self._audio.get("playing") or self._bluetooth.get("playing"):
            return MediaPlayerState.PLAYING
        return MediaPlayerState.IDLE

    @property
    def volume_level(self) -> float | None:
        volume = self._audio.get("volume")
        return None if volume is None else max(0.0, min(1.0, int(volume) / 100))

    @property
    def media_title(self) -> str | None:
        if self._audio.get("playing"):
            return str(self._audio["playing"])
        if self._bluetooth.get("playing"):
            return f"Bluetooth: {self._bluetooth.get('connected_name') or 'phone'}"
        return None

    @property
    def media_content_id(self) -> str | None:
        source = self._audio.get("source")
        return str(source) if source else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        source = None
        if self._audio.get("playing"):
            source = "stream" if self._audio.get("stream") else "file"
        elif self._bluetooth.get("playing"):
            source = "bluetooth"
        return {
            "source": source,
            "loop": bool(self._audio.get("loop")),
            "bluetooth_device": self._bluetooth.get("connected_name"),
        }

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        if media_source.is_media_source_id(media_id):
            item = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = item.url
        url = async_process_play_media_url(self.hass, media_id)
        extra = kwargs.get("extra") or {}
        body: dict[str, Any] = {"play": url}
        if extra.get("loop"):
            body["loop"] = True
        if extra.get("volume") is not None:
            body["volume"] = int(extra["volume"])
        self.bridge_set("audio", await self.bridge.audio(**body))

    async def async_media_stop(self) -> None:
        self.bridge_set("audio", await self.bridge.audio(stop=True))

    async def async_media_pause(self) -> None:
        await self.async_media_stop()

    async def async_set_volume_level(self, volume: float) -> None:
        self.bridge_set(
            "audio",
            await self.bridge.audio(volume=int(round(volume * 100)), feedback=False),
        )

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )
