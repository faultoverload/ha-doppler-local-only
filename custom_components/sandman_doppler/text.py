"""Text platform: weather location and each alarm's name."""

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
from .const import DOMAIN
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
        setup_alarm_entities(hass, entry, device, async_add_devices, [DopplerAlarmName])

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
