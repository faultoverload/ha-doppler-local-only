"""Sensor platform for Doppler Sandman."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from doppyler.const import ATTR_LIGHT_SENSOR_VALUE, ATTR_WEATHER, ATTR_WIFI
from doppyler.model.doppler import Doppler

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import DopplerDataUpdateCoordinator
from .const import DOMAIN
from .entity import DopplerBridgeEntity, DopplerEntity

_LOGGER = logging.getLogger(__name__)


@dataclass
class DopplerSensorEntityDescription(SensorEntityDescription):
    """Class describing Doppler sensor entities."""

    state_key: str | None = None
    state_func: Callable[[Any], Any] | None = None
    icon_func: Callable[[Any], Any] | None = None


SENSOR_ENTITY_DESCRIPTIONS = [
    DopplerSensorEntityDescription(
        "Light Detected",
        name="Light Detected",
        icon="mdi:lightbulb",
        state_class=SensorStateClass.MEASUREMENT,
        state_key=ATTR_LIGHT_SENSOR_VALUE,
        state_func=lambda x: round(x, 2),
    ),
    DopplerSensorEntityDescription(
        "Wifi: Connected Since",
        name="Wifi Connected Since",
        icon="mdi:connection",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.TIMESTAMP,
        state_key=ATTR_WIFI,
        state_func=lambda x: dt_util.now() - x.uptime,
    ),
    DopplerSensorEntityDescription(
        "Wifi: SSID",
        name="Wifi SSID",
        icon="mdi:wifi",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_key=ATTR_WIFI,
        state_func=lambda x: x.ssid,
    ),
    DopplerSensorEntityDescription(
        "Wifi: Signal Strength",
        name="Wifi Signal Strength",
        icon_func=lambda x: f"mdi:wifi-strength-{(x // 25) + 1 if x else 'outline'}",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=PERCENTAGE,
        state_key=ATTR_WIFI,
        state_func=lambda x: int(x.signal_strength),
    ),
]


@dataclass
class DopplerBridgeSensorEntityDescription(SensorEntityDescription):
    """A sensor from GET /<dsn>/bridge (open firmware only)."""

    state_path: tuple[str, ...] = ()
    state_func: Callable[[Any], Any] = lambda x: x
    attributes_path: tuple[str, ...] | None = None  # a dict copied into the attributes
    attribute_keys: tuple[str, ...] | None = None  # only these keys of it


def _next_alarm(nxt: Any) -> str | None:
    if not isinstance(nxt, dict):
        return "none"
    text = f"{int(nxt.get('hour', 0)):02d}:{int(nxt.get('minute', 0)):02d}"
    days = int(nxt.get("days", 0) or 0)
    return f"{text} +{days}d" if days else text


BRIDGE_SENSOR_ENTITY_DESCRIPTIONS = [
    DopplerBridgeSensorEntityDescription(
        "Voice Assistant",
        name="Voice: Assistant State",
        icon="mdi:account-voice",
        state_path=("voice", "state"),
        attributes_path=("voice",),
        attribute_keys=(
            "muted",
            "pipeline",
            "reason",
            "wake_word",
            "text",
            "response",
            "satellite",
        ),
    ),
    DopplerBridgeSensorEntityDescription(
        "Alarm State",
        name="Alarm: State",
        icon="mdi:alarm",
        state_path=("alarms", "state"),
        attributes_path=("alarms",),
        attribute_keys=("alarm", "snooze_until", "armed", "count"),
    ),
    DopplerBridgeSensorEntityDescription(
        "Next Alarm",
        name="Alarm: Next",
        icon="mdi:alarm-check",
        state_path=("alarms", "next"),
        state_func=_next_alarm,
        attributes_path=("alarms", "next"),
    ),
    DopplerBridgeSensorEntityDescription(
        "Weather",
        name="Weather: Shown",
        icon="mdi:weather-partly-cloudy",
        state_path=("weather", "value"),
        attributes_path=("weather",),
        attribute_keys=(
            "scale",
            "icons",
            "condition",
            "place",
            "location",
            "mode",
            "enabled",
            "fetched_at",
            "error",
        ),
    ),
    DopplerBridgeSensorEntityDescription(
        "Bridge Version",
        name="Bridge Version",
        icon="mdi:source-branch",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_path=("software",),
    ),
    DopplerBridgeSensorEntityDescription(
        "Bridge Uptime",
        name="Bridge Uptime",
        icon="mdi:timer-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        state_path=("system", "uptime_s"),
    ),
    DopplerBridgeSensorEntityDescription(
        "CPU Temperature",
        name="CPU Temperature",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        state_path=("system", "cpu_temp_c"),
    ),
    DopplerBridgeSensorEntityDescription(
        "Memory Used",
        name="Memory Used",
        icon="mdi:memory",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        state_path=("system", "mem_used_pct"),
    ),
    DopplerBridgeSensorEntityDescription(
        "PSoC Firmware",
        name="PSoC Firmware",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_path=("psoc", "firmware"),
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_devices: AddEntitiesCallback
) -> None:
    """Setup sensor platform."""

    @callback
    def async_add_device(device: Doppler) -> None:
        """Add Doppler sensor entities."""
        coordinator: DopplerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
            device.dsn
        ]
        entities = [
            DopplerSensor(coordinator, entry, device, description)
            for description in SENSOR_ENTITY_DESCRIPTIONS
        ]
        entities.extend(
            DopplerBridgeSensor(coordinator, entry, device, description)
            for description in BRIDGE_SENSOR_ENTITY_DESCRIPTIONS
        )
        async_add_devices(entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{DOMAIN}_{entry.entry_id}_device_added", async_add_device
        )
    )


class DopplerSensor(DopplerEntity[DopplerSensorEntityDescription], SensorEntity):
    """Doppler sensor class."""

    @property
    def icon(self) -> str | None:
        """Return the icon for the entity."""
        if self.ed.icon_func and self.native_value is not None:
            return self.ed.icon_func(self.native_value)
        return super().icon

    @property
    def native_value(self) -> Any:
        """Return the native value of the sensor."""
        if self.ed.state_key is None:
            return None
        raw_value = self.device_data.get(self.ed.state_key)
        if raw_value is None:
            return None
        return self.ed.state_func(raw_value)


class DopplerBridgeSensor(
    DopplerBridgeEntity[DopplerBridgeSensorEntityDescription], SensorEntity
):
    """A sensor from the bridge state."""

    @property
    def native_value(self) -> Any:
        value = self.bridge_get(*self.ed.state_path)
        if value is None and self.ed.state_func is not _next_alarm:
            return None
        return self.ed.state_func(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.ed.attributes_path:
            return None
        node = self.bridge_get(*self.ed.attributes_path)
        if not isinstance(node, dict):
            return None
        keys = self.ed.attribute_keys or tuple(node)
        return {k: node[k] for k in keys if k in node}
