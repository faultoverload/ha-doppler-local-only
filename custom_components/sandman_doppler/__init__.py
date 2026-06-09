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
from homeassistant.helpers.network import get_url
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, CONF_HOST, CONF_PORT, CONF_LOCAL_KEY, CONF_DSN
from .http import DopplerWebhookView
from .services import DopplerServices

SCAN_INTERVAL = timedelta(seconds=60)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SIREN,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Sandman Doppler component."""
    hass.http.register_view(DopplerWebhookView())
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
    client = DopplerClient(
        "", "", client_session=session, local_api_semaphore_limit=1
    )

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

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
    """Reload config entry."""
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
        self._entry = entry
        self._entities_created = False
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
