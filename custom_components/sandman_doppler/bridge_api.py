"""Calls to the open-firmware bridge's local API (faultoverload/sandman-doppler).

The stock endpoints are covered by doppyler; these are the bridge's own:
GET /<dsn>/bridge (everything in one call), the bridge/* controls, voice/*,
alarms/snooze|dismiss|ring and hardware/day-mode with a mode. They go through
the same nonce/HMAC session doppyler keeps for the device.
"""

from __future__ import annotations

from typing import Any

from doppyler.exceptions import DopplerException, UnknownException
from doppyler.model.doppler import Doppler

SECTION_FOR_TOPIC = {
    "voice/state": "voice",
    "alarms/state": "alarms",
    "weather/state": "weather",
    "mode/state": "display",
    "lightbar/state": "lightbar",
    "audio/state": "audio",
    "bluetooth/state": "bluetooth",
    "system": "system",
}
# pushed states that only carry part of the section: merged into the polled one
MERGED_SECTIONS = {"display"}
ANIMATIONS = ["off", "sweep", "pulse", "comet", "sparkle"]
DAY_NIGHT_MODES = ["day", "night", "auto"]


class BridgeApi:
    """Thin async wrapper over the bridge endpoints for one Doppler."""

    def __init__(self, doppler: Doppler) -> None:
        self._doppler = doppler

    async def _call(
        self, endpoint: str, method: str = "GET", data: dict | None = None
    ) -> Any:
        return await self._doppler._call_local_api(endpoint, method=method, data=data)

    async def get_state(self) -> dict[str, Any] | None:
        """The aggregated state, or None when the device is not running the bridge."""
        try:
            return await self._call("bridge")
        except UnknownException:
            return None  # stock firmware / older bridge: 404

    async def set_webhook(self, url: str) -> dict[str, Any]:
        return await self._call("bridge/webhook", "PUT", {"url": url})

    # display
    async def set_clock(self, enabled: bool) -> dict[str, Any]:
        return await self._call("bridge/clock", "PUT", {"enabled": bool(enabled)})

    async def set_day_night_mode(self, mode: str) -> dict[str, Any]:
        return await self._call("hardware/day-mode", "PUT", {"mode": mode})

    async def set_lightbar(
        self,
        state: str,
        color: tuple[int, int, int] | None = None,
        brightness: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"state": state}
        if color is not None:
            body["color"] = {"r": color[0], "g": color[1], "b": color[2]}
        if brightness is not None:
            body["brightness"] = int(brightness)
        return await self._call("bridge/lightbar", "PUT", body)

    async def set_animation(self, name: str) -> dict[str, Any]:
        return await self._call("bridge/animation", "PUT", {"name": name})

    async def audio(self, **body: Any) -> dict[str, Any]:
        return await self._call("bridge/audio", "PUT", body)

    # voice
    async def voice_settings(self, **changes: Any) -> dict[str, Any]:
        return await self._call("voice/settings", "PUT", changes)

    async def voice_session(self, action: str) -> dict[str, Any]:
        return await self._call("voice/session", "POST", {"action": action})

    async def voice_say(
        self, url: str | None = None, path: str | None = None, volume: int | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if url:
            body["url"] = url
        if path:
            body["path"] = path
        if volume is not None:
            body["volume"] = int(volume)
        return await self._call("voice/tts", "POST", body)

    # alarms
    async def alarm_snooze(self, minutes: int | None = None) -> dict[str, Any]:
        return await self._call(
            "alarms/snooze", "POST", {"minutes": minutes} if minutes else {}
        )

    async def alarm_dismiss(self) -> dict[str, Any]:
        return await self._call("alarms/dismiss", "POST", {})

    async def alarm_ring(self, alarm_id: int | None = None) -> dict[str, Any]:
        return await self._call(
            "alarms/ring", "POST", {"id": alarm_id} if alarm_id is not None else {}
        )

    # weather fed by Home Assistant
    async def set_external_weather(
        self,
        value: float | None,
        scale: str,
        condition: str | None,
        wind_kmh: float | None = None,
        place: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "value": value,
            "scale": scale,
            "condition": condition or "",
        }
        if wind_kmh is not None:
            body["wind_kmh"] = wind_kmh
        if place:
            body["place"] = place
        return await self._call("weather/external", "PUT", body)

    async def clear_external_weather(self) -> dict[str, Any]:
        return await self._call("weather/external", "PUT", {"clear": True})

    async def set_small_display_colors(self, **colors: Any) -> dict[str, Any]:
        """weather=(r, g, b) / seconds=(r, g, b); None follows the clock's colour."""
        body = {k: (list(v) if v is not None else None) for k, v in colors.items()}
        return await self._call("bridge/colors", "PUT", body)

    async def bluetooth(self, **body: Any) -> dict[str, Any]:
        """{"pairing": bool} | {"disconnect": True} | {"forget": address} -> the bluetooth state."""
        return await self._call("bridge/bluetooth", "PUT", body)

    async def set_alarm_stream(self, alarm_id: int, url: str) -> dict[str, Any]:
        """A stream URL the alarm plays instead of its sound ("" = the sound file)."""
        return await self._call(
            f"bridge/alarms/{int(alarm_id)}", "PUT", {"stream": url}
        )

    async def set_digits(
        self, text: str, color: tuple[int, int, int] | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text}
        if color is not None:
            body["color"] = list(color)
        return await self._call("bridge/digits", "PUT", body)
