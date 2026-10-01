"""Text platform: weather location, custom digits, each alarm's name and stream URL."""

from __future__ import annotations

from doppyler.const import ATTR_WEATHER
from doppyler.model.doppler import Doppler

from homeassistant.components.text import TextEntity, TextEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DopplerDataUpdateCoordinator
from .alarm_entities import DopplerAlarmEntity, setup_alarm_entities
from .const import ATTR_BRIDGE, DOMAIN
from .entity import DopplerBridgeEntity, DopplerEntity


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
                DopplerWeatherLocationText(
                    coordinator,
                    entry,
                    device,
                    TextEntityDescription(
                        "Weather: Location",
                        name="Weather: Location",
                        icon="mdi:earth",
                        entity_category=EntityCategory.CONFIG,
                    ),
                )
            ]
        )
        setup_alarm_entities(
            hass,
            entry,
            device,
            async_add_devices,
            [DopplerAlarmName, DopplerAlarmStream],
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{DOMAIN}_{entry.entry_id}_device_added", async_add_device
        )
    )


class DopplerWeatherLocationText(DopplerEntity[TextEntityDescription], TextEntity):
    """The place the device fetches weather for (city, 'City, ST' or 'lat,lon')."""

    _attr_native_max = 64

    @property
    def native_value(self) -> str | None:
        weather = self.device_data.get(ATTR_WEATHER)
        return getattr(weather, "location", None) if weather else None

    async def async_set_value(self, value: str) -> None:
        self.device_data[ATTR_WEATHER] = await self.device.set_weather_configuration(
            location=value.strip()
        )
        self.async_write_ha_state()


class DopplerAlarmName(DopplerAlarmEntity, TextEntity):
    suffix = "name"
    label = "Name"
    _attr_icon = "mdi:rename"
    _attr_native_max = 32

    @property
    def native_value(self) -> str | None:
        return self.alarm.name if self.alarm else None

    async def async_set_value(self, value: str) -> None:
        await self._save(name=value.strip() or f"Alarm {self.alarm_id}")


class DopplerAlarmStream(DopplerAlarmEntity, TextEntity):
    """An http(s) URL the alarm plays instead of its sound: an internet radio stream, an audio
    file or a podcast feed (its newest episode plays). The sound file is the fallback when it
    will not play. Open-firmware bridge only.
    """

    suffix = "stream"
    label = "Stream URL"
    _attr_icon = "mdi:radio"
    _attr_native_max = 255
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def _streams(self) -> dict | None:
        bridge = (self.coordinator.data or {}).get(ATTR_BRIDGE)
        alarms = bridge.get("alarms") if isinstance(bridge, dict) else None
        return alarms.get("streams") if isinstance(alarms, dict) else None

    @property
    def available(self) -> bool:
        return super().available and self._streams is not None

    @property
    def native_value(self) -> str | None:
        streams = self._streams
        return str(streams.get(str(self.alarm_id), "")) if streams is not None else None

    async def async_set_value(self, value: str) -> None:
        url = value.strip()
        if url and not url.lower().startswith(("http://", "https://")):
            raise ValueError("the stream must be an http(s) URL")
        result = await self.coordinator.bridge.set_alarm_stream(self.alarm_id, url)
        streams = self._streams
        if streams is not None:
            streams[str(self.alarm_id)] = result.get("stream", url)
        self.async_write_ha_state()


class DopplerCustomDigitsText(DopplerBridgeEntity[TextEntityDescription], TextEntity):
    """Up to four characters (plus a colon) on the main display until cleared: digits and
    the letters/symbols a 7-segment digit can show (A b C c d E F G H h I J L n O o P q r
    S t U u y Z - _ = ° ' " [ ] ?). Empty text hands the digits back to the clock."""

    _attr_native_max = 5

    @property
    def native_value(self) -> str | None:
        return self.bridge_get("display", "custom_text", default="")

    async def async_set_value(self, value: str) -> None:
        text = value.strip()
        if len(text.replace(":", "")) > 4:
            raise ValueError("at most four characters plus a colon")
        result = await self.bridge.set_digits(text)
        self.bridge_set("display", {"custom_text": result.get("custom_text", text)})
