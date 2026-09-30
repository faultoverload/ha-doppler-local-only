"""Time platform: each alarm's time."""

from __future__ import annotations

from datetime import time as dt_time

from doppyler.model.doppler import Doppler

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alarm_entities import DopplerAlarmEntity, setup_alarm_entities
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_devices: AddEntitiesCallback
) -> None:
    @callback
    def async_add_device(device: Doppler) -> None:
        setup_alarm_entities(hass, entry, device, async_add_devices, [DopplerAlarmTime])

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{DOMAIN}_{entry.entry_id}_device_added", async_add_device
        )
    )


class DopplerAlarmTime(DopplerAlarmEntity, TimeEntity):
    suffix = "time"
    label = "Time"
    _attr_icon = "mdi:clock-edit"

    @property
    def native_value(self) -> dt_time | None:
        return self.alarm.time if self.alarm else None

    async def async_set_value(self, value: dt_time) -> None:
        await self._save(time=value.replace(second=0, microsecond=0))
