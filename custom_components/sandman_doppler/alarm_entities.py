"""Per-alarm entities (time, sound, repeat, volume, name, delete) for the Doppler's alarms.

Each stored alarm gets its own entities so alarms can be configured from the
UI instead of the add_alarm/update_alarm services. Entities appear when an
alarm shows up in doppyler's alarm list and go away when it is deleted.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import time as dt_time
import functools
import logging
from typing import Any

from doppyler.model.alarm import Alarm, AlarmSource, RepeatDayOfWeek
from doppyler.model.color import Color
from doppyler.model.doppler import Doppler

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import DopplerDataUpdateCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

WEEKDAYS = [
    RepeatDayOfWeek.MONDAY,
    RepeatDayOfWeek.TUESDAY,
    RepeatDayOfWeek.WEDNESDAY,
    RepeatDayOfWeek.THURSDAY,
    RepeatDayOfWeek.FRIDAY,
]
WEEKEND = [RepeatDayOfWeek.SATURDAY, RepeatDayOfWeek.SUNDAY]
REPEAT_OPTIONS = {
    "Once": [],
    "Every day": WEEKDAYS + WEEKEND,
    "Weekdays": WEEKDAYS,
    "Weekends": WEEKEND,
}
REPEAT_CUSTOM = "Custom"
DEFAULT_SOUND = "Classic"


def repeat_option(alarm: Alarm) -> str:
    days = set(alarm.repeat or [])
    for name, option_days in REPEAT_OPTIONS.items():
        if days == set(option_days):
            return name
    return REPEAT_CUSTOM


def new_alarm(device: Doppler) -> Alarm:
    """A disabled 07:00 alarm on the first free id."""
    used = set(device.alarms)
    alarm_id = next(i for i in range(1, 256) if i not in used)
    sound = (
        DEFAULT_SOUND
        if not device.alarm_sounds or DEFAULT_SOUND in device.alarm_sounds
        else device.alarm_sounds[0]
    )
    return Alarm(
        id=alarm_id,
        name=f"Alarm {alarm_id}",
        time=dt_time(7, 0),
        repeat=[],
        color=Color(255, 255, 255),
        volume=50,
        status="unarmed",
        src=AlarmSource.APP,
        sound=sound,
    )


def setup_alarm_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: Doppler,
    async_add_devices: AddEntitiesCallback,
    factories: list[
        Callable[[DopplerDataUpdateCoordinator, ConfigEntry, Doppler, Alarm], Any]
    ],
) -> None:
    """Create the given per-alarm entities for existing alarms and for alarms added later."""
    coordinator: DopplerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        device.dsn
    ]

    def build(alarm: Alarm) -> list[Any]:
        return [factory(coordinator, entry, device, alarm) for factory in factories]

    async_add_devices([e for alarm in device.alarms.values() for e in build(alarm)])
    entry.async_on_unload(
        device.on_alarm_added(lambda alarm: async_add_devices(build(alarm)))
    )


class DopplerAlarmEntity(CoordinatorEntity[DopplerDataUpdateCoordinator]):
    """Base for one alarm's entities; `suffix` distinguishes them."""

    _attr_has_entity_name = True
    suffix = ""
    label = ""

    def __init__(
        self,
        coordinator: DopplerDataUpdateCoordinator,
        config_entry: ConfigEntry,
        device: Doppler,
        alarm: Alarm,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self.device = device
        self.alarm_id = alarm.id
        self._attr_unique_id = slugify(
            f"{config_entry.unique_id}_{device.dsn}_alarm_{alarm.id}_{self.suffix}"
        )
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device.dsn)})

    @property
    def alarm(self) -> Alarm | None:
        return self.device.alarms.get(self.alarm_id)

    @property
    def name(self) -> str:
        return f"Alarm {self.alarm_id}: {self.label}"

    @property
    def available(self) -> bool:
        return super().available and self.alarm is not None

    async def _save(self, **changes: Any) -> None:
        alarm = self.alarm
        if alarm is None:
            return
        for key, value in changes.items():
            setattr(alarm, key, value)
        await self.device.update_alarm(alarm.id, alarm)
        self.async_write_ha_state()

    @callback
    def _alarm_deleted(self) -> None:
        """The alarm is gone from the device: drop the entity from the registry, not just the state machine."""
        ent_reg = er.async_get(self.hass)
        if self.entity_id and ent_reg.async_get(self.entity_id):
            ent_reg.async_remove(self.entity_id)
        else:
            self.hass.async_create_task(self.async_remove(force_remove=True))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_{self.device.dsn}_alarm_{self.alarm_id}_removed",
                self._alarm_deleted,
            )
        )
