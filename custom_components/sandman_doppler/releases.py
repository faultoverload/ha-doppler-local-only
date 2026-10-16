"""The branches the clock's bridge software can be updated to (GitHub prereleases built by CI).

The sandman-doppler repo's workflow (.github/workflows/bridge-release.yml) publishes, for every
branch, one prerelease tagged `bridge-<branch>` named after the branch, with three assets:
release.tar.gz, release.tar.gz.sig and manifest.json (one entry: version, build, commit, sha256).
This module lists them (one API call, then the small manifests, cached by release), and serves
a release's files to the clock through http.DopplerUpdateView so the clock only ever talks to Home
Assistant. Nothing is verified here: the clock checks the signature against its own public key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import DEFAULT_UPDATE_BRANCH, DEFAULT_UPDATE_REPO, SIGNAL_RELEASES

_LOGGER = logging.getLogger(__name__)

TAG_PREFIX = "bridge-"
TARBALL = "release.tar.gz"
SIGNATURE = "release.tar.gz.sig"
MANIFEST = "manifest.json"
FILES = (MANIFEST, TARBALL, SIGNATURE)
REFRESH_EVERY = timedelta(minutes=30)
MAX_MANIFEST = 1024 * 1024
MAX_RELEASE = 64 * 1024 * 1024
TIMEOUT = aiohttp.ClientTimeout(total=60)


class ReleasesError(Exception):
    """GitHub could not be read or a release is unusable."""


@dataclass(frozen=True)
class Channel:
    """The latest build of one branch."""

    branch: str
    tag: str
    key: str  # the release's name in its manifest (<version>-<build>): what the clock installs
    version: str
    build: int
    commit: str
    sha256: str
    size: int
    changelog: str
    url: str  # the release page
    assets: dict[str, str] = field(
        default_factory=dict, compare=False
    )  # file name -> API url

    @property
    def label(self) -> str:
        return f"{self.branch}@{self.commit}"


def parse_channel(release: dict[str, Any], manifest: dict[str, Any]) -> Channel:
    """A Channel from a GitHub release object and the manifest.json asset it carries."""
    assets = {
        a["name"]: a["url"]
        for a in release.get("assets", [])
        if "name" in a and "url" in a
    }
    if not all(name in assets for name in FILES):
        raise ReleasesError(f"release {release.get('tag_name')} is missing assets")
    try:
        key = str(manifest["latest"])
        entry = manifest["releases"][key]
        return Channel(
            branch=str(release.get("name") or entry.get("branch") or ""),
            tag=str(release["tag_name"]),
            key=key,
            version=str(entry.get("version") or ""),
            build=int(entry["build"]),
            commit=str(entry.get("commit") or ""),
            sha256=str(entry["sha256"]).lower(),
            size=int(entry.get("size") or 0),
            changelog=str(entry.get("changelog") or ""),
            url=str(release.get("html_url") or ""),
            assets=assets,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReleasesError(
            f"release {release.get('tag_name')} has an unusable manifest: {exc}"
        ) from exc


def device_manifest(channel: Channel) -> dict[str, Any]:
    """The manifest the clock reads: this one release, its files next to the manifest."""
    return {
        "latest": channel.key,
        "releases": {
            channel.key: {
                "version": channel.version,
                "build": channel.build,
                "sha256": channel.sha256,
                "size": channel.size,
                "changelog": channel.changelog,
                "commit": channel.commit,
                "branch": channel.branch,
                "url": TARBALL,
                "signature": SIGNATURE,
            }
        },
    }


class ReleasesIndex:
    """What can be installed, refreshed every REFRESH_EVERY; also remembers each clock's chosen branch."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.repo = DEFAULT_UPDATE_REPO
        self.token: str | None = None
        self.channels: dict[str, Channel] = {}
        self.last_error: str | None = None
        self._manifests: dict[tuple[str, str], dict[str, Any]] = (
            {}
        )  # (tag, updated_at) -> manifest
        self._selected: dict[str, str] = {}
        self._unsub = None

    # configuration --------------------------------------------------------------

    def configure(self, repo: str | None, token: str | None) -> None:
        repo = (repo or DEFAULT_UPDATE_REPO).strip()
        token = (token or "").strip() or None
        if (repo, token) != (self.repo, self.token):
            self.repo, self.token = repo, token
            self.channels, self._manifests = {}, {}
            self.hass.async_create_task(self.async_refresh())

    def start(self) -> None:
        if self._unsub is None:
            self._unsub = async_track_time_interval(
                self.hass, self._tick, REFRESH_EVERY
            )
            self.hass.async_create_task(self.async_refresh())

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def _tick(self, _now) -> None:
        await self.async_refresh()

    # chosen branch --------------------------------------------------------------

    def selected(self, dsn: str) -> str:
        return self._selected.get(dsn, DEFAULT_UPDATE_BRANCH)

    def select(self, dsn: str, branch: str) -> None:
        self._selected[dsn] = branch
        async_dispatcher_send(self.hass, SIGNAL_RELEASES)

    def channel_for(self, dsn: str) -> Channel | None:
        return self.channels.get(self.selected(dsn))

    def branches(self) -> list[str]:
        """main first, then the rest alphabetically."""
        names = sorted(
            self.channels, key=lambda n: (n != DEFAULT_UPDATE_BRANCH, n.lower())
        )
        return names

    def by_tag(self, tag: str) -> Channel | None:
        return next((c for c in self.channels.values() if c.tag == tag), None)

    # GitHub ---------------------------------------------------------------------

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": "ha-sandman-doppler",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _get_bytes(self, url: str, accept: str, limit: int) -> bytes:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                url, headers=self._headers(accept), timeout=TIMEOUT
            ) as resp:
                if resp.status in (401, 403, 404):
                    hint = (
                        "the token is not accepted or lacks read access to contents"
                        if self.token
                        else "the repository is private or does not exist: add a GitHub token in the integration's options"
                    )
                    raise ReleasesError(
                        f"GitHub answered {resp.status} for {url} ({hint})"
                    )
                if resp.status != 200:
                    raise ReleasesError(f"GitHub answered {resp.status} for {url}")
                if int(resp.headers.get("Content-Length") or 0) > limit:
                    raise ReleasesError(f"{url} is larger than {limit} bytes")
                data = await resp.read()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ReleasesError(f"cannot reach GitHub: {exc}") from exc
        if len(data) > limit:
            raise ReleasesError(f"{url} is larger than {limit} bytes")
        return data

    async def async_refresh(self) -> None:
        """Re-read the release list. Keeps what it had if GitHub cannot be reached."""
        import json

        try:
            raw = await self._get_bytes(
                f"https://api.github.com/repos/{self.repo}/releases?per_page=100",
                "application/vnd.github+json",
                8 * MAX_MANIFEST,
            )
            releases = json.loads(raw)
            channels: dict[str, Channel] = {}
            for release in releases:
                if not str(release.get("tag_name", "")).startswith(
                    TAG_PREFIX
                ) or release.get("draft"):
                    continue
                cache_key = (
                    release["tag_name"],
                    str(release.get("updated_at") or release.get("published_at")),
                )
                try:
                    manifest = self._manifests.get(cache_key)
                    if manifest is None:
                        asset = next(
                            (
                                a
                                for a in release.get("assets", [])
                                if a.get("name") == MANIFEST
                            ),
                            None,
                        )
                        if asset is None:
                            continue
                        manifest = json.loads(
                            await self._get_bytes(
                                asset["url"], "application/octet-stream", MAX_MANIFEST
                            )
                        )
                        self._manifests[cache_key] = manifest
                    channel = parse_channel(release, manifest)
                except (ReleasesError, ValueError) as exc:
                    _LOGGER.debug(
                        "Skipping release %s: %s", release.get("tag_name"), exc
                    )
                    continue
                if channel.branch:
                    channels[channel.branch] = channel
            self._manifests = {
                k: v
                for k, v in self._manifests.items()
                if k[0] in {c.tag for c in channels.values()}
            }
            self.channels = channels
            self.last_error = None
        except (ReleasesError, ValueError, TypeError) as exc:
            # warn when the problem is new or changes, not every 30 minutes
            level = logging.DEBUG if str(exc) == self.last_error else logging.WARNING
            self.last_error = str(exc)
            _LOGGER.log(level, "Could not list Sandman Doppler releases: %s", exc)
        async_dispatcher_send(self.hass, SIGNAL_RELEASES)

    async def async_fetch(self, channel: Channel, filename: str) -> bytes:
        """A release file's bytes (the tarball, its signature)."""
        if filename not in (TARBALL, SIGNATURE):
            raise ReleasesError(f"no such file: {filename}")
        return await self._get_bytes(
            channel.assets[filename], "application/octet-stream", MAX_RELEASE
        )
