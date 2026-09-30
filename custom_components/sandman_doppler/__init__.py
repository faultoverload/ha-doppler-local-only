"""The Sandman Doppler integration (local-only)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from doppyler.client import DopplerClient
from doppyler.exceptions import DopplerException
from doppyler.model.doppler import Doppler

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.network import get_url
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bridge_api import SECTION_FOR_TOPIC, BridgeApi
from .const import (
    ALEXA_UNIQUE_ID_SUFFIXES,
    ATTR_BRIDGE,
    CONF_DSN,
    CONF_HOST,
    CONF_LOCAL_KEY,
    CONF_PORT,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_SCALE,
    DOMAIN,
)
from .http import DopplerEventView, DopplerWebhookView
from .services import DopplerServices

SCAN_INTERVAL = timedelta(seconds=60)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SIREN,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.TIME,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Sandman Doppler component."""
    hass.http.register_view(DopplerWebhookView())
    hass.http.register_view(DopplerEventView())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up config entry (local-only, no cloud auth)."""
    hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})

    # Read local config from entry
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    local_key = entry.data[CONF_LOCAL_KEY]
    dsn = entry.data[CONF_DSN]

    session = async_get_clientsession(hass)

    # Create a minimal DopplerClient (no cloud auth) — needed only as HTTP
    # transport for Doppler._call_local_api() which uses client.request()
    client = DopplerClient("", "", client_session=session, local_api_semaphore_limit=1)

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # The Alexa entities of earlier versions: there is no Alexa on the open firmware
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent.unique_id and ent.unique_id.endswith(ALEXA_UNIQUE_ID_SUFFIXES):
            _LOGGER.info("Removing stale Alexa entity %s", ent.entity_id)
            ent_reg.async_remove(ent.entity_id)

    if not hass.data[DOMAIN][entry.entry_id].get("platform_setup_complete"):
        hass.data[DOMAIN][entry.entry_id]["platform_setup_complete"] = True
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Build device info (DSN is the only field we must get right; others are cosmetic)
    device_info = {
        "serialNum": dsn,
        "mfgrName": "Palo Alto Innovation",
        "modelNum": "Doppler",
        "firmware": "",
        "hardware": "",
        "software": "",
    }

    # Build local info from config
    local_info = {
        "localkey": local_key,
        "ipAddie": host,
        "port": port,
    }

    # Create the Doppler object directly — no cloud calls
    doppler = Doppler(
        client,
        dsn,
        device_info,
        local_info,
        local_control=True,
        local_api_semaphore_limit=1,
    )

    # Register device and create coordinator
    dev_entry = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, doppler.dsn)},
        manufacturer=doppler.device_info.manufacturer,
        model=doppler.device_info.model_number,
        sw_version=doppler.device_info.software_version,
        hw_version=doppler.device_info.firmware_version,
        name=doppler.name,
    )

    hass.data[DOMAIN][entry.entry_id][doppler.dsn] = coordinator = (
        DopplerDataUpdateCoordinator(hass, entry, client, doppler, dev_entry)
    )
    hass.async_create_task(coordinator.async_refresh())

    # Store doppler in client.devices so DopplerServices lookups resolve
    client.devices[dsn] = doppler

    DopplerServices(hass, ent_reg, dev_reg, client).async_register()

    coordinator.async_setup_weather_feed()
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    unloaded = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
            ]
        )
    )
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry (also after an options change)."""
    if not entry.options.get(CONF_WEATHER_ENTITY):
        # the weather feed was switched off: hand the display back to the device's own fetcher
        for entry_data in [hass.data.get(DOMAIN, {}).get(entry.entry_id, {})]:
            for value in entry_data.values():
                if isinstance(value, DopplerDataUpdateCoordinator):
                    try:
                        await value.bridge.clear_external_weather()
                    except DopplerException:
                        pass
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


class DopplerDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching data from the API."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: DopplerClient,
        doppler: Doppler,
        device_entry: dr.DeviceEntry,
    ) -> None:
        """Initialize."""
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_{doppler.dsn}", update_interval=SCAN_INTERVAL
        )
        self.data: dict[str, Any] = {}
        self.api = client
        self.doppler = doppler
        self.bridge = BridgeApi(doppler)
        self.device_entry = device_entry
        self._entry = entry
        self._entities_created = False
        self._bridge_seen: bool | None = None
        base_url = get_url(
            self.hass,
            require_ssl=False,
            require_standard_port=False,
            allow_internal=True,
            allow_external=True,
            allow_cloud=True,
            allow_ip=True,
            prefer_external=False,
            prefer_cloud=False,
        )
        self._webhook_url = (
            f"{base_url}/api/sandman_doppler/smart_button/{device_entry.id}"
        )
        self._event_url = f"{base_url}/api/sandman_doppler/event/{device_entry.id}"

    async def _reschedule_refresh(self) -> None:
        """Reschedule refresh due to failure."""
        _LOGGER.debug("Update failed, scheduling a new one in 15 seconds")
        await asyncio.sleep(15)
        await self.async_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library."""
        _LOGGER.debug(
            "Getting update for device %s (%s)", self.doppler.name, self.doppler.dsn
        )
        try:
            data = await self.doppler.get_all_data()
            data[ATTR_BRIDGE] = await self.bridge.get_state()
        except DopplerException as exc:
            _LOGGER.debug(
                "Exception received during update for device %s (%s): %s: %s",
                self.doppler.name,
                self.doppler.dsn,
                type(exc).__name__,
                exc,
            )
            if not self._entities_created:
                self.hass.async_create_task(self._reschedule_refresh())
            raise UpdateFailed() from exc
        else:
            _LOGGER.debug(
                "Finished getting update for device %s (%s)",
                self.doppler.name,
                self.doppler.dsn,
            )
        bridge = data.get(ATTR_BRIDGE)
        if bridge is not None and self._bridge_seen is not True:
            # Open-firmware bridge: get pushed events, and show its version on the device
            self._bridge_seen = True
            try:
                await self.bridge.set_webhook(self._event_url)
            except DopplerException as exc:
                _LOGGER.warning(
                    "Could not register the event webhook on %s: %s",
                    self.doppler.dsn,
                    exc,
                )
            if bridge.get("software"):
                dr.async_get(self.hass).async_update_device(
                    self.device_entry.id, sw_version=bridge["software"]
                )
        elif bridge is None and self._bridge_seen is None:
            self._bridge_seen = False
            _LOGGER.info(
                "%s runs the stock firmware (no /bridge endpoint): bridge entities stay unavailable",
                self.doppler.dsn,
            )
        if not self.data:
            self._entities_created = True
            await asyncio.gather(
                *[
                    self.doppler.set_smart_button_configuration(
                        button_num, url=self._webhook_url, command="HA"
                    )
                    for button_num in range(1, 3)
                ]
            )
            async_dispatcher_send(
                self.hass, f"{DOMAIN}_{self._entry.entry_id}_device_added", self.doppler
            )
        return data

    # ── Home Assistant weather entity -> the device's temperature display ──

    @callback
    def async_setup_weather_feed(self) -> None:
        """Push the configured HA weather entity's temperature and condition to the device
        whenever it changes (options: weather_entity, weather_scale)."""
        entity_id = self._entry.options.get(CONF_WEATHER_ENTITY)
        if not entity_id:
            return

        @callback
        def _changed(event) -> None:
            self.hass.async_create_task(self._push_weather(entity_id))

        self._entry.async_on_unload(
            async_track_state_change_event(self.hass, [entity_id], _changed)
        )
        self.hass.async_create_task(self._push_weather(entity_id))

    async def _push_weather(self, entity_id: str) -> None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return
        temp = state.attributes.get("temperature")
        unit = str(
            state.attributes.get("temperature_unit")
            or self.hass.config.units.temperature_unit
        )
        scale_opt = self._entry.options.get(CONF_WEATHER_SCALE, "auto")
        scale = "C" if "C" in unit else "F"
        if scale_opt in ("F", "C") and temp is not None and scale != scale_opt:
            temp = temp * 9 / 5 + 32 if scale_opt == "F" else (temp - 32) * 5 / 9
            scale = scale_opt
        wind = state.attributes.get("wind_speed")
        wind_unit = str(state.attributes.get("wind_speed_unit") or "km/h")
        if wind is not None and "mph" in wind_unit:
            wind = float(wind) * 1.609
        elif wind is not None and "m/s" in wind_unit:
            wind = float(wind) * 3.6
        try:
            result = await self.bridge.set_external_weather(
                temp, scale, state.state, wind_kmh=wind, place=state.name
            )
        except DopplerException as exc:
            _LOGGER.debug("Weather push to %s failed: %s", self.doppler.dsn, exc)
            return
        if self.data and isinstance(self.data.get(ATTR_BRIDGE), dict):
            self.data[ATTR_BRIDGE]["weather"] = result
            self.async_set_updated_data(self.data)

    @callback
    def apply_bridge_update(self, topic: str, payload: Any) -> None:
        """A pushed state from the bridge's event webhook (DopplerEventView)."""
        if not self.data or self.data.get(ATTR_BRIDGE) is None:
            return
        bridge: dict[str, Any] = self.data[ATTR_BRIDGE]
        section = SECTION_FOR_TOPIC.get(topic)
        if section and isinstance(payload, dict):
            bridge[section] = payload
        elif topic == "psoc/battery" and isinstance(payload, dict):
            bridge.setdefault("psoc", {})["battery"] = payload.get("value")
        elif topic == "psoc/firmware" and isinstance(payload, dict):
            bridge.setdefault("psoc", {})["firmware"] = payload.get("version")
        elif topic == "sensors/light" and isinstance(payload, dict):
            bridge.setdefault("psoc", {})["light"] = payload.get("value")
        else:
            return
        self.async_set_updated_data(self.data)
