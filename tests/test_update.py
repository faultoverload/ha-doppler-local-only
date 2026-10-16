"""The Bridge Software update entity: versions, the install call sequence and following the clock
through a restart, a rollback or a refusal (update.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from doppyler.exceptions import DopplerException
import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.sandman_doppler import update as upd
from custom_components.sandman_doppler.const import DATA_RELEASES
from custom_components.sandman_doppler.releases import Channel, ReleasesIndex

CH = Channel(
    branch="main",
    tag="bridge-main",
    key="16-200",
    version="16",
    build=200,
    commit="def5678",
    sha256="b" * 64,
    size=1,
    changelog="Fix the thing",
    url="https://gh/rel",
    assets={},
)


def running(name="16-100", branch="main", commit="abc1234"):
    return {
        "name": name,
        "branch": branch,
        "commit": commit,
        "version": "16",
        "build": 100,
    }


def status(state="idle", name="16-100", trial=False, pct=0, error=""):
    return {
        "state": state,
        "pct": pct,
        "error": error,
        "trial": trial,
        "running": running(name),
    }


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(upd, "POLL_EVERY", 0)


def make_entity(channel=CH, update_block="default", scripted=None):
    index = ReleasesIndex(MagicMock())
    index.channels = {channel.branch: channel} if channel else {}
    index.hass = SimpleNamespace(async_create_task=lambda c: c.close())
    hass = SimpleNamespace(data={DATA_RELEASES: index})
    bridge = SimpleNamespace(
        update_set_url=AsyncMock(),
        update_check=AsyncMock(),
        update_install=AsyncMock(),
        update_status=AsyncMock(side_effect=scripted or [status()]),
    )
    block = status() if update_block == "default" else update_block
    coordinator = SimpleNamespace(
        data={"bridge": {"update": block} if block is not None else {}},
        bridge=bridge,
        base_url="https://ha.example",
        device_entry=SimpleNamespace(id="devid"),
        async_request_refresh=AsyncMock(),
        last_update_success=True,
    )
    ent = upd.DopplerBridgeUpdate.__new__(upd.DopplerBridgeUpdate)
    ent.hass, ent.coordinator, ent.device = (
        hass,
        coordinator,
        SimpleNamespace(dsn="dsn1"),
    )
    ent.async_write_ha_state = MagicMock()
    return ent, bridge, index


def test_versions_are_branch_at_commit():
    ent, _, _ = make_entity()
    assert (
        ent.installed_version == "main@abc1234" and ent.latest_version == "main@def5678"
    )
    assert ent.version_is_newer(ent.latest_version, ent.installed_version) is True
    assert ent.version_is_newer("main@abc1234", "main@abc1234") is False
    assert (
        ent.release_summary == "Fix the thing" and ent.release_url == "https://gh/rel"
    )
    assert ent.extra_state_attributes["branch"] == "main"


def test_a_clock_flashed_over_ssh_reads_as_local_and_old_bridges_are_unavailable():
    block = status()
    block["running"] = {"name": "baseline", "commit": "9e3152e", "version": "16"}
    ent, _, _ = make_entity(update_block=block)
    assert ent.installed_version == "local@9e3152e"
    old, _, _ = make_entity(update_block=None)  # a bridge from before self-update
    assert old.bridge_get("update") is None


def test_no_published_build_means_unknown_latest():
    ent, _, _ = make_entity(channel=None)
    assert ent.latest_version is None and ent.release_summary is None


async def test_install_sends_the_sequence_and_waits_for_the_commit():
    ent, bridge, _ = make_entity(
        scripted=[
            status("downloading", pct=40),
            status("restarting", pct=100),
            DopplerException("restarting"),
            status("idle", "16-200", trial=True),
            status("idle", "16-200", trial=False),
        ]
    )
    await ent.async_install(None, False)
    url = (
        "https://ha.example/api/sandman_doppler/updates/devid/bridge-main/manifest.json"
    )
    bridge.update_set_url.assert_awaited_once_with(url)
    bridge.update_check.assert_awaited_once()
    bridge.update_install.assert_awaited_once_with("16-200", "b" * 64)
    assert ent._attr_in_progress is False and ent._attr_update_percentage is None
    ent.coordinator.async_request_refresh.assert_awaited_once()


async def test_install_reports_a_rollback():
    ent, _, _ = make_entity(
        scripted=[status("restarting", pct=100), status("idle", "16-100")]
    )
    with pytest.raises(HomeAssistantError, match="rolled back"):
        await ent.async_install(None, False)
    assert ent._attr_in_progress is False
    ent.coordinator.async_request_refresh.assert_awaited_once()


async def test_install_reports_a_rollback_after_the_trial_started():
    ent, _, _ = make_entity(
        scripted=[
            status("restarting"),
            status("idle", "16-200", trial=True),
            status("idle", "16-100"),
        ]
    )
    with pytest.raises(HomeAssistantError, match="rolled back"):
        await ent.async_install(None, False)


async def test_install_reports_a_clock_error():
    ent, _, _ = make_entity(
        scripted=[status("error", error="the release signature does not verify")]
    )
    with pytest.raises(HomeAssistantError, match="signature"):
        await ent.async_install(None, False)


async def test_clock_refusing_the_install_call():
    ent, bridge, _ = make_entity()
    bridge.update_install.side_effect = DopplerException(
        "400: release 16-200 is already running"
    )
    with pytest.raises(HomeAssistantError, match="refused"):
        await ent.async_install(None, False)
    assert ent._attr_in_progress is False


async def test_install_without_a_published_build():
    ent, bridge, index = make_entity(channel=None)
    index.async_refresh = AsyncMock()
    index.last_error = "GitHub answered 404"
    with pytest.raises(HomeAssistantError, match="No build of branch 'main'"):
        await ent.async_install(None, False)
    bridge.update_set_url.assert_not_awaited()


async def test_install_uses_the_selected_branch(monkeypatch):
    monkeypatch.setattr(
        "custom_components.sandman_doppler.releases.async_dispatcher_send",
        lambda *a: None,
    )
    other = Channel(
        **{
            **CH.__dict__,
            "branch": "feature/x",
            "tag": "bridge-feature-x",
            "key": "16-300",
        }
    )
    ent, bridge, index = make_entity(scripted=[status("idle", "16-300", trial=False)])
    index.channels["feature/x"] = other
    index.select("dsn1", "feature/x")
    await ent.async_install(None, False)
    assert bridge.update_set_url.await_args.args[0].endswith(
        "/devid/bridge-feature-x/manifest.json"
    )
    bridge.update_install.assert_awaited_once_with("16-300", "b" * 64)


async def test_clock_that_never_comes_back(monkeypatch):
    monkeypatch.setattr(upd, "GONE_TOLERANCE", -1)
    ent, _, _ = make_entity(scripted=[DopplerException("gone")] * 3)
    with pytest.raises(HomeAssistantError, match="stopped answering"):
        await ent.async_install(None, False)
