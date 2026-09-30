"""Button platform for Doppler Sandman: alarm snooze/dismiss and voice start (open-firmware bridge)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from doppyler.model.doppler import Doppler

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DopplerDataUpdateCoordinator
from .const import DOMAIN
from .entity import DopplerBridgeEntity


@dataclass
class DopplerButtonEntityDescription(ButtonEntityDescription):
    """A button on the bridge's API."""

    press_func: Callable[[Any], Coroutine[Any, Any, dict]] | None = None
    section: str | None = None


BUTTON_ENTITY_DESCRIPTIONS = [
    DopplerButtonEntityDescription(
        "Snooze Alarm",
        name="Snooze Alarm",
        icon="mdi:alarm-snooze",
        press_func=lambda api: api.alarm_snooze(),
        section="alarms",
    ),
    DopplerButtonEntityDescription(
        "Dismiss Alarm",
        name="Dismiss Alarm",
        icon="mdi:alarm-off",
        press_func=lambda api: api.alarm_dismiss(),
        section="alarms",
    ),
    DopplerButtonEntityDescription(
        "Start Listening",
        name="Start Listening",
        icon="mdi:microphone-message",
        press_func=lambda api: api.voice_session("start"),
        section="voice",
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_devices: AddEntitiesCallback
) -> None:
    """Setup button platform."""

    @callback
    def async_add_device(device: Doppler) -> None:
        coordinator: DopplerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
            device.dsn
        ]
        async_add_devices(
            DopplerButton(coordinator, entry, device, description)
            for description in BUTTON_ENTITY_DESCRIPTIONS
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{DOMAIN}_{entry.entry_id}_device_added", async_add_device
        )
    )


class DopplerButton(DopplerBridgeEntity[DopplerButtonEntityDescription], ButtonEntity):
    """A bridge button."""

    async def async_press(self) -> None:
        result = await self.ed.press_func(self.bridge)
        if self.ed.section and isinstance(result, dict):
            result = {k: v for k, v in result.items() if k != "ok"}
            self.bridge_set(self.ed.section, result)
