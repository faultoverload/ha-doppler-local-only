"""Release index (releases.py) and the view that serves a build to the clock (http.py)."""

from __future__ import annotations

import json

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

from custom_components.sandman_doppler.const import DATA_RELEASES, DOMAIN
from custom_components.sandman_doppler.http import DopplerUpdateView
from custom_components.sandman_doppler.releases import (
    ReleasesError,
    ReleasesIndex,
    device_manifest,
    parse_channel,
)

API = "https://api.github.com/repos/faultoverload/sandman-doppler/releases?per_page=100"


def manifest(key="16-100", commit="abc1234", branch="main", sha="a" * 64):
    return {
        "latest": key,
        "releases": {
            key: {
                "version": "16",
                "build": int(key.split("-")[1]),
                "sha256": sha,
                "size": 11,
                "commit": commit,
                "branch": branch,
                "changelog": "subject",
                "url": "16-100/release.tar.gz",
                "signature": "16-100/release.tar.gz.sig",
            }
        },
    }


def release(
    branch,
    rid,
    tag=None,
    assets=("release.tar.gz", "release.tar.gz.sig", "manifest.json"),
):
    tag = tag or "bridge-" + branch.replace("/", "-")
    return {
        "tag_name": tag,
        "name": branch,
        "draft": False,
        "updated_at": f"2026-10-15T00:00:0{rid}Z",
        "html_url": f"https://github.com/x/y/releases/tag/{tag}",
        "assets": [
            {"name": n, "url": f"https://api.github.com/assets/{rid}/{n}"}
            for n in assets
        ],
    }


def mock_github(aioclient_mock, releases, manifests):
    aioclient_mock.get(API, text=json.dumps(releases))
    for rid, m in manifests.items():
        aioclient_mock.get(
            f"https://api.github.com/assets/{rid}/manifest.json", text=json.dumps(m)
        )


def test_parse_channel_and_device_manifest():
    ch = parse_channel(
        release("feature/x", 1), manifest("16-200", "def5678", "feature/x")
    )
    assert (ch.branch, ch.key, ch.commit, ch.label) == (
        "feature/x",
        "16-200",
        "def5678",
        "feature/x@def5678",
    )
    m = device_manifest(ch)
    assert (
        m["latest"] == "16-200" and m["releases"]["16-200"]["url"] == "release.tar.gz"
    )
    assert (
        m["releases"]["16-200"]["signature"] == "release.tar.gz.sig"
        and m["releases"]["16-200"]["branch"] == "feature/x"
    )
    with pytest.raises(ReleasesError, match="missing assets"):
        parse_channel(release("b", 1, assets=("release.tar.gz",)), manifest())
    with pytest.raises(ReleasesError, match="unusable"):
        parse_channel(release("b", 1), {"latest": "nope", "releases": {}})


async def test_refresh_lists_branches_main_first(hass: HomeAssistant, aioclient_mock):
    mock_github(
        aioclient_mock,
        [
            release("zeta", 1),
            release("main", 2),
            release("alpha", 3),
            {"tag_name": "v1.0", "name": "v1.0", "assets": []},
        ],  # not a bridge build: ignored
        {1: manifest("16-1", "z"), 2: manifest("16-2", "m"), 3: manifest("16-3", "a")},
    )
    index = ReleasesIndex(hass)
    await index.async_refresh()
    assert index.branches() == ["main", "alpha", "zeta"]
    assert (
        index.channels["main"].commit == "m"
        and index.by_tag("bridge-alpha").branch == "alpha"
    )
    assert index.last_error is None
    assert index.channel_for("dsn1").branch == "main"  # default selection
    index.select("dsn1", "alpha")
    assert (
        index.channel_for("dsn1").branch == "alpha"
        and index.channel_for("dsn2").branch == "main"
    )


async def test_manifests_are_cached_per_release(hass: HomeAssistant, aioclient_mock):
    mock_github(aioclient_mock, [release("main", 2)], {2: manifest("16-2")})
    index = ReleasesIndex(hass)
    await index.async_refresh()
    n = aioclient_mock.call_count
    await index.async_refresh()
    assert aioclient_mock.call_count == n + 1  # only the list again, not the manifest


async def test_a_failed_refresh_keeps_the_last_list(
    hass: HomeAssistant, aioclient_mock
):
    mock_github(aioclient_mock, [release("main", 2)], {2: manifest("16-2")})
    index = ReleasesIndex(hass)
    await index.async_refresh()
    aioclient_mock.clear_requests()
    aioclient_mock.get(API, status=403)
    await index.async_refresh()
    assert index.branches() == ["main"] and "403" in index.last_error


async def test_token_is_sent_and_broken_releases_are_skipped(
    hass: HomeAssistant, aioclient_mock
):
    mock_github(
        aioclient_mock,
        [release("main", 1), release("bad", 2, assets=("manifest.json",))],
        {1: manifest("16-1"), 2: manifest("16-2")},
    )
    index = ReleasesIndex(hass)
    index.token = "ghp_secret"
    await index.async_refresh()
    assert index.branches() == ["main"]
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer ghp_secret"


async def test_fetch_only_serves_the_tarball_and_signature(
    hass: HomeAssistant, aioclient_mock
):
    mock_github(aioclient_mock, [release("main", 1)], {1: manifest("16-1")})
    aioclient_mock.get(
        "https://api.github.com/assets/1/release.tar.gz", content=b"TARBALL"
    )
    index = ReleasesIndex(hass)
    await index.async_refresh()
    ch = index.channels["main"]
    assert await index.async_fetch(ch, "release.tar.gz") == b"TARBALL"
    with pytest.raises(ReleasesError):
        await index.async_fetch(ch, "../../etc/passwd")


async def test_view_serves_manifest_and_files_to_a_known_device_only(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock,
    mock_config_entry_factory=None,
):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    assert await async_setup_component(hass, "http", {})
    hass.http.register_view(DopplerUpdateView())
    index = hass.data[DATA_RELEASES] = ReleasesIndex(hass)
    mock_github(aioclient_mock, [release("main", 1)], {1: manifest("16-1")})
    aioclient_mock.get(
        "https://api.github.com/assets/1/release.tar.gz", content=b"TARBALL"
    )
    aioclient_mock.get(
        "https://api.github.com/assets/1/release.tar.gz.sig", content=b"SIG"
    )
    await index.async_refresh()
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "dsn1")}
    )
    other = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("hue", "x")}
    )

    client = await hass_client_no_auth()
    base = f"/api/sandman_doppler/updates/{dev.id}/bridge-main"
    resp = await client.get(f"{base}/manifest.json")
    assert (
        resp.status == 200
        and (await resp.json())["releases"]["16-1"]["sha256"] == "a" * 64
    )
    assert await (await client.get(f"{base}/release.tar.gz")).read() == b"TARBALL"
    assert await (await client.get(f"{base}/release.tar.gz.sig")).read() == b"SIG"
    assert (
        await client.get(f"{base}/something-else")
    ).status == 404  # not a release file
    assert (
        await client.get(
            f"/api/sandman_doppler/updates/{dev.id}/bridge-nope/manifest.json"
        )
    ).status == 404
    assert (
        await client.get(
            f"/api/sandman_doppler/updates/deadbeef/bridge-main/manifest.json"
        )
    ).status == 404
    assert (
        await client.get(
            f"/api/sandman_doppler/updates/{other.id}/bridge-main/manifest.json"
        )
    ).status == 404
