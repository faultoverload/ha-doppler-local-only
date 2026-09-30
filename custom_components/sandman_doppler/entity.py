"""Doppler light platform."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from doppyler.model.doppler import Doppler

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo, EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import DopplerDataUpdateCoordinator
from .bridge_api import BridgeApi
from .const import ATTR_BRIDGE, DOMAIN

_EntityDescriptionT = TypeVar("_EntityDescriptionT", bound="EntityDescription")


class DopplerEntity(
    CoordinatorEntity[DopplerDataUpdateCoordinator], Generic[_EntityDescriptionT]
):
    """Base class for a Doppler entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DopplerDataUpdateCoordinator,
        config_entry: ConfigEntry,
        device: Doppler,
        description: _EntityDescriptionT,
    ):
        super().__init__(coordinator)
        self.entity_description: _EntityDescriptionT = description
        self.ed: _EntityDescriptionT = description
        self.config_entry = config_entry
        self.device = device

        self._attr_unique_id = slugify(
            f"{self.config_entry.unique_id}_{self.device.dsn}_{self.ed.key}"
        )
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self.device.dsn)})

    @property
    def device_data(self) -> dict[str, Any]:
        """Return device data."""
        return self.coordinator.data


class DopplerBridgeEntity(DopplerEntity[_EntityDescriptionT]):
    """An entity backed by the bridge's GET /<dsn>/bridge state (open firmware only)."""

    @property
    def bridge_data(self) -> dict[str, Any] | None:
        data = self.coordinator.data or {}
        return data.get(ATTR_BRIDGE)

    @property
    def bridge(self) -> BridgeApi:
        return self.coordinator.bridge

    @property
    def available(self) -> bool:
        return super().available and self.bridge_data is not None

    def bridge_get(self, *path: str, default: Any = None) -> Any:
        """bridge_get("voice", "state") -> the nested value, or default."""
        node: Any = self.bridge_data
        for key in path:
            if not isinstance(node, dict):
                return default
            node = node.get(key)
        return default if node is None else node

    def bridge_set(self, section: str, value: Any) -> None:
        """Merge a control call's answer into its section and write state."""
        if self.bridge_data is not None and isinstance(value, dict):
            current = self.bridge_data.get(section)
            if isinstance(current, dict):
                current.update(value)
            else:
                self.bridge_data[section] = dict(value)
        self.async_write_ha_state()
