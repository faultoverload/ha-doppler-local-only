"""Update platform: install the bridge software of a branch of the sandman-doppler repo on the clock.

The clock replaces its own code from a signed build (updater.py on the device, rollback included);
this entity picks the build (the branch chosen in "Update: Branch", main by default), points the
clock at Home Assistant as the place to fetch it from (http.DopplerUpdateView), asks it to install
that exact release and watches until it is running and committed, or has rolled itself back.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from doppyler.exceptions import DopplerException
from doppyler.model.doppler import Doppler

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DopplerDataUpdateCoordinator
from .const import DATA_RELEASES, DOMAIN, SIGNAL_RELEASES
from .entity import DopplerBridgeEntity
from .releases import Channel, ReleasesIndex

_LOGGER = logging.getLogger(__name__)

POLL_EVERY = 2  # seconds between looks at the clock while it installs
INSTALL_TIMEOUT = 360  # a crash loop rolls back in ~25 s, a hang in ~2 min
COMMIT_TIMEOUT = (
    150  # after it runs the new release: the clock commits after 60 s of health
)
GONE_TOLERANCE = 90  # the clock is unreachable for a few seconds while it restarts


def running_label(update: dict[str, Any] | None) -> str | None:
    """'main@abc1234' for the release the clock runs ('local@...' when it has no branch, e.g. an SSH push)."""
    running = (update or {}).get("running") or {}
    commit = running.get("commit") or running.get("version")
    if not commit:
        return None
    return f"{running.get('branch') or 'local'}@{commit}"


class DopplerBridgeUpdate(DopplerBridgeEntity[UpdateEntityDescription], UpdateEntity):
    """The bridge software on one clock."""

    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )
    _attr_title = "Sandman Doppler bridge"

    @property
    def _index(self) -> ReleasesIndex:
        return self.hass.data[DATA_RELEASES]

    @property
    def _channel(self) -> Channel | None:
        return self._index.channel_for(self.device.dsn)

    @property
    def available(self) -> bool:
        # without the update block the bridge predates self-update: nothing to install onto
        return super().available and self.bridge_get("update") is not None

    @property
    def installed_version(self) -> str | None:
        return running_label(self.bridge_get("update"))

    @property
    def latest_version(self) -> str | None:
        channel = self._channel
        return channel.label if channel else None

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Builds are `branch@commit`, not versions: any difference is an update (also a downgrade
        to main from a branch, or the other way round)."""
        return latest_version != installed_version

    @property
    def release_summary(self) -> str | None:
        channel = self._channel
        return channel.changelog[:255] if channel and channel.changelog else None

    @property
    def release_url(self) -> str | None:
        channel = self._channel
        return channel.url if channel and channel.url else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        update = self.bridge_get("update", default={})
        return {
            "branch": self._index.selected(self.device.dsn),
            "branches": self._index.branches(),
            "running_release": (update.get("running") or {}).get("name"),
            "clock_state": update.get("state"),
            "on_trial": update.get("trial"),
            "last_error": update.get("error") or self._index.last_error or None,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_RELEASES, self._releases_changed)
        )

    @callback
    def _releases_changed(self) -> None:
        self.async_write_ha_state()

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        index = self._index
        branch = index.selected(self.device.dsn)
        channel = index.channels.get(branch)
        if channel is None:
            await index.async_refresh()
            channel = index.channels.get(branch)
        if channel is None:
            raise HomeAssistantError(
                f"No build of branch '{branch}' is published"
                + (f" ({index.last_error})" if index.last_error else "")
            )
        url = (
            f"{self.coordinator.base_url}/api/sandman_doppler/updates/"
            f"{self.coordinator.device_entry.id}/{channel.tag}/manifest.json"
        )
        self._attr_in_progress = True
        self._attr_update_percentage = 0
        self.async_write_ha_state()
        try:
            try:
                await self.bridge.update_set_url(url)
                await self.bridge.update_check()
                await self.bridge.update_install(channel.key, channel.sha256)
            except DopplerException as exc:
                raise HomeAssistantError(
                    f"The clock refused the update: {exc}"
                ) from exc
            await self._watch(channel)
        finally:
            self._attr_in_progress = False
            self._attr_update_percentage = None
            await self.coordinator.async_request_refresh()

    async def _watch(self, channel: Channel) -> None:
        """Follow the clock until it runs `channel` and has committed it. Raises when it refuses,
        rolls back or does not answer for too long."""
        started = time.monotonic()
        restarted = False  # saw the old process say it was restarting
        reached_at: float | None = None
        last_seen = started
        while True:
            await asyncio.sleep(POLL_EVERY)
            now = time.monotonic()
            if reached_at is None and now - started > INSTALL_TIMEOUT:
                raise HomeAssistantError("The clock did not confirm the update in time")
            if reached_at is not None and now - reached_at > COMMIT_TIMEOUT:
                raise HomeAssistantError(
                    "The clock runs the new build but has not committed it"
                )
            try:
                status = await self.bridge.update_status()
            except DopplerException:
                if now - last_seen > GONE_TOLERANCE:
                    raise HomeAssistantError(
                        "The clock stopped answering during the update"
                    ) from None
                continue
            last_seen = now
            state = status.get("state")
            running = status.get("running") or {}
            if state == "error":
                raise HomeAssistantError(
                    f"The clock could not install it: {status.get('error')}"
                )
            if state == "downloading":
                self._attr_update_percentage = status.get("pct", 0)
                self.async_write_ha_state()
            elif state in ("verifying", "installing"):
                self._attr_update_percentage = None
            if state == "restarting":
                restarted = True
                continue
            if running.get("name") == channel.key:
                if reached_at is None:
                    reached_at = now
                    self._attr_update_percentage = 100
                    self.async_write_ha_state()
                if not status.get("trial"):
                    return
            elif reached_at is not None or (restarted and state == "idle"):
                # a different release after the restart: the launcher gave up on the new one
                raise HomeAssistantError(
                    "The new build did not stay up, so the clock rolled back to "
                    f"{running_label({'running': running})}"
                )


UPDATE_DESCRIPTION = UpdateEntityDescription(
    key="bridge_update",
    name="Bridge Software",
    device_class=UpdateDeviceClass.FIRMWARE,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_devices: AddEntitiesCallback
) -> None:
    """Set up the update platform."""

    @callback
    def async_add_device(device: Doppler) -> None:
        coordinator: DopplerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
            device.dsn
        ]
        async_add_devices(
            [DopplerBridgeUpdate(coordinator, entry, device, UPDATE_DESCRIPTION)]
        )

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{DOMAIN}_{entry.entry_id}_device_added", async_add_device
        )
    )
