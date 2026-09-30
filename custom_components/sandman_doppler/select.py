"""Select platform for Doppler Sandman."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import Enum
from typing import Any
import zoneinfo

from doppyler.const import (
    ATTR_SOUND_PRESET,
    ATTR_TIME_MODE,
    ATTR_TIMEZONE,
    ATTR_WEATHER,
)
from doppyler.model.doppler import Doppler
from doppyler.model.sound import SoundPreset
from doppyler.model.weather import WeatherMode

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DopplerDataUpdateCoordinator
from .const import DOMAIN
from .alarm_entities import (
    REPEAT_CUSTOM,
    REPEAT_OPTIONS,
    DopplerAlarmEntity,
    repeat_option,
    setup_alarm_entities,
)
from .bridge_api import ANIMATIONS, DAY_NIGHT_MODES
from .entity import DopplerBridgeEntity, DopplerEntity
from .helpers import get_enum_from_name, normalize_enum_name


@dataclass
class DopplerEnumSelectEntityDescription(SelectEntityDescription):
    """Doppler Enum Select Entity Description."""

    enum_cls: Enum | None = None
    state_key: str | None = None
    state_func: Callable[[Any], int] = lambda x: x
    set_value_func: Callable[[Doppler, int], Coroutine[Any, Any, Enum]] = None


@dataclass
class DopplerSelectEntityDescription(SelectEntityDescription):
    """Class to describe Doppler select entities."""

    state_key: str | None = None
    options_func: Callable[[Doppler], list[str]] = None
    state_func: Callable[[Any], str] = lambda x: str(x)
    set_value_func: Callable[[Doppler, str], Coroutine[Any, Any, Any]] = None


ENUM_SELECT_ENTITY_DESCRIPTIONS = [
    DopplerEnumSelectEntityDescription(
        "Sound Preset",
        name="Sound Preset",
        icon="mdi:music",
        entity_category=EntityCategory.CONFIG,
        enum_cls=SoundPreset,
        state_key=ATTR_SOUND_PRESET,
        set_value_func=lambda dev, val: dev.set_sound_preset(val),
    ),
    DopplerEnumSelectEntityDescription(
        "Weather: Mode",
        name="Weather: Mode",
        icon="mdi:weather-partly-snowy-rainy",
        entity_category=EntityCategory.CONFIG,
        enum_cls=WeatherMode,
        state_key=ATTR_WEATHER,
        state_func=lambda x: x.mode,
        set_value_func=lambda dev, val: dev.set_weather_configuration(mode=val),
    ),
]


@dataclass
class DopplerBridgeSelectEntityDescription(SelectEntityDescription):
    """A select on the bridge's own API (open firmware only)."""

    state_path: tuple[str, ...] = ()
    section: str = ""
    options_list: list[str] | None = None
    set_func: Callable[[Any, str], Coroutine[Any, Any, dict]] | None = None
    result_func: Callable[[dict], dict] = (
        lambda x: x
    )  # shape the answer like the section
    entity_category: EntityCategory | None = None


BRIDGE_SELECT_ENTITY_DESCRIPTIONS = [
    DopplerBridgeSelectEntityDescription(
        "Day/Night Selection",
        name="Day/Night: Mode (day, night, auto)",
        icon="mdi:theme-light-dark",
        entity_category=EntityCategory.CONFIG,
        state_path=("display", "day_night_mode"),
        section="display",
        options_list=DAY_NIGHT_MODES,
        set_func=lambda api, val: api.set_day_night_mode(val),
        result_func=lambda r: {
            "day_night_mode": r.get("mode"),
            "day": str(r.get("isDayMode")).lower() == "true",
        },
    ),
    DopplerBridgeSelectEntityDescription(
        "Lightbar Animation",
        name="Display: Lightbar Animation",
        icon="mdi:animation-play",
        state_path=("lightbar", "animation"),
        section="lightbar",
        options_list=ANIMATIONS,
        set_func=lambda api, val: api.set_animation(val),
    ),
]


SELECT_ENTITY_DESCRIPTIONS = [
    DopplerSelectEntityDescription(
        "Time Mode",
        name="Time Mode",
        icon="mdi:clock",
        entity_category=EntityCategory.CONFIG,
        state_key=ATTR_TIME_MODE,
        options_func=lambda _: ["12", "24"],
        set_value_func=lambda dev, val: dev.set_time_mode(int(val)),
    ),
    DopplerSelectEntityDescription(
        "Timezone",
        name="Timezone",
        icon="mdi:map-clock",
        entity_category=EntityCategory.CONFIG,
        state_key=ATTR_TIMEZONE,
        options_func=lambda _: sorted(list(zoneinfo.available_timezones())),
        set_value_func=lambda dev, val: dev.set_timezone(zoneinfo.ZoneInfo(val)),
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_devices: AddEntitiesCallback
) -> None:
    """Setup select platform."""

    @callback
    def async_add_device(device: Doppler) -> None:
        """Add Doppler select entities."""
        coordinator: DopplerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
            device.dsn
        ]
        entities = [
            DopplerSelect(coordinator, entry, device, description)
            for description in SELECT_ENTITY_DESCRIPTIONS
        ]
        entities.extend(
            (
                DopplerEnumSelect(coordinator, entry, device, description)
                for description in ENUM_SELECT_ENTITY_DESCRIPTIONS
            )
        )
        entities.extend(
            DopplerBridgeSelect(coordinator, entry, device, description)
            for description in BRIDGE_SELECT_ENTITY_DESCRIPTIONS
        )
        async_add_devices(entities)
        setup_alarm_entities(
            hass,
            entry,
            device,
            async_add_devices,
            [DopplerAlarmSoundSelect, DopplerAlarmRepeatSelect],
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{DOMAIN}_{entry.entry_id}_device_added", async_add_device
        )
    )


class DopplerEnumSelect(
    DopplerEntity[DopplerEnumSelectEntityDescription], SelectEntity
):
    """Doppler Select class for enum attributes."""

    @property
    def options(self) -> list[str]:
        """Return a set of selectable options."""
        return [normalize_enum_name(enum_val) for enum_val in self.ed.enum_cls]

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        if self.ed.state_key is None:
            return None
        current_option = self.device_data.get(self.ed.state_key)
        if current_option is None:
            return None
        return normalize_enum_name(self.ed.state_func(current_option))

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        enum_val = get_enum_from_name(self.ed.enum_cls, option)
        self.device_data[self.ed.state_key] = await self.ed.set_value_func(
            self.device, enum_val
        )
        self.async_write_ha_state()


class DopplerSelect(DopplerEntity[DopplerSelectEntityDescription], SelectEntity):
    """Doppler Select class."""

    @property
    def options(self) -> list[str]:
        """Return a set of selectable options."""
        return self.ed.options_func(self.device)

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        if self.ed.state_key is None:
            return None
        raw_value = self.device_data.get(self.ed.state_key)
        if raw_value is None:
            return None
        return self.ed.state_func(raw_value)

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        self.device_data[self.ed.state_key] = await self.ed.set_value_func(
            self.device, option
        )
        self.async_write_ha_state()


class DopplerBridgeSelect(
    DopplerBridgeEntity[DopplerBridgeSelectEntityDescription], SelectEntity
):
    """Day/night mode and lightbar animation, on the bridge's API."""

    @property
    def entity_category(self) -> EntityCategory | None:
        return self.ed.entity_category

    @property
    def options(self) -> list[str]:
        return list(self.ed.options_list or [])

    @property
    def current_option(self) -> str | None:
        value = self.bridge_get(*self.ed.state_path)
        if value is None:
            return None
        value = str(value)
        return value if value in self.options else None

    async def async_select_option(self, option: str) -> None:
        result = await self.ed.set_func(self.bridge, option)
        self.bridge_set(
            self.ed.section,
            self.ed.result_func(result) if isinstance(result, dict) else None,
        )


class DopplerAlarmSoundSelect(DopplerAlarmEntity, SelectEntity):
    suffix = "sound"
    label = "Sound"
    _attr_icon = "mdi:music"

    @property
    def options(self) -> list[str]:
        sounds = list(self.device.alarm_sounds or [])
        current = self.alarm.sound if self.alarm else None
        if current and current not in sounds:
            sounds.append(current)
        return sounds

    @property
    def current_option(self) -> str | None:
        return self.alarm.sound if self.alarm else None

    async def async_select_option(self, option: str) -> None:
        await self._save(sound=option)


class DopplerAlarmRepeatSelect(DopplerAlarmEntity, SelectEntity):
    suffix = "repeat"
    label = "Repeat"
    _attr_icon = "mdi:calendar-refresh"
    _attr_options = list(REPEAT_OPTIONS) + [REPEAT_CUSTOM]

    @property
    def current_option(self) -> str | None:
        return repeat_option(self.alarm) if self.alarm else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.alarm:
            return None
        return {"days": [day.value for day in self.alarm.repeat]}

    async def async_select_option(self, option: str) -> None:
        if option == REPEAT_CUSTOM:
            return  # keep the current custom day set (use update_alarm for other combinations)
        await self._save(repeat=list(REPEAT_OPTIONS[option]))
