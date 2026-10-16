"""Saving the options must work: Home Assistant raises ValueError (HTTP 500) when an entry has an
update listener and its options flow is an OptionsFlowWithReload (config_entries.async_finish_flow).
"""

from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sandman_doppler import async_setup_entry
from custom_components.sandman_doppler.const import DOMAIN


async def test_setup_registers_no_update_listener_so_options_can_be_saved(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "10.0.2.17", "port": 5443, "local_key": "k", "dsn": "dsn1"},
        unique_id="dsn1",
    )
    entry.add_to_hass(hass)
    hass.config.internal_url = "http://ha.local:8123"
    with (
        patch(
            "custom_components.sandman_doppler.DopplerDataUpdateCoordinator.async_refresh",
            new=AsyncMock(),
        ),
        patch("custom_components.sandman_doppler.DopplerServices"),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
    ):
        assert await async_setup_entry(hass, entry)
    assert not entry.update_listeners

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch.object(hass.config_entries, "async_schedule_reload"):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"weather_scale": "auto", "update_repo": "a/b", "github_token": "t"},
        )
    assert result["type"] == "create_entry"
    assert entry.options["github_token"] == "t"
