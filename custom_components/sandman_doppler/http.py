"""HTTP views for Sandman Doppler."""

from __future__ import annotations

from http import HTTPStatus
import logging

from aiohttp.web import Request, Response

from homeassistant.components.http.view import HomeAssistantView
from homeassistant.const import ATTR_DEVICE_ID, ATTR_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    ATTR_BRIDGE,
    ATTR_DATA,
    ATTR_DOPPLER_NAME,
    ATTR_DSN,
    ATTR_TOPIC,
    DOMAIN,
    EVENT_ALARM_EVENT,
    EVENT_BUTTON_EVENT,
    EVENT_BUTTON_PRESSED,
    EVENT_VOICE_REQUEST,
)
from .bridge_api import SECTION_FOR_TOPIC

_LOGGER = logging.getLogger(__name__)


class DopplerWebhookView(HomeAssistantView):
    """Provide a page for the device to call."""

    requires_auth = False
    cors_allowed = True
    url = r"/api/sandman_doppler/smart_button/{device_id}"
    name = "api:sandman_doppler:smart_button"

    def __init__(self) -> None:
        """Initialize view."""
        super().__init__()
        self._dev_reg: dr.DeviceRegistry | None = None

    async def post(self, request: Request, device_id: str) -> Response:
        """Respond to requests from the device."""
        hass: HomeAssistant = request.app["hass"]
        if not self._dev_reg:
            self._dev_reg = dr.async_get(hass)

        device = self._dev_reg.async_get(device_id)
        if not device:
            _LOGGER.error("Device not found: %s", device_id)
            return Response(status=HTTPStatus.OK)
        if not (
            identifier := next(
                (
                    identifier
                    for identifier in device.identifiers
                    if identifier[0] == DOMAIN
                ),
                None,
            )
        ):
            _LOGGER.error("Device not a Sandman Doppler device: %s", device_id)
            return Response(status=HTTPStatus.OK)

        data = await request.json()
        if not (dsn := data.get(ATTR_DSN)):
            _LOGGER.error("Invalid request: %s", data)
            return Response(status=HTTPStatus.OK)
        if identifier[1] != dsn:
            _LOGGER.error(
                "DSN sent (%s) does not match device entry: %s (%s)",
                dsn,
                device_id,
                identifier[1],
            )
            return Response(status=HTTPStatus.OK)

        hass.bus.async_fire(
            EVENT_BUTTON_PRESSED,
            {
                **data,
                ATTR_DOPPLER_NAME: device.name,
                ATTR_NAME: device.name_by_user or device.name,
                ATTR_DEVICE_ID: device_id,
            },
        )
        return Response(status=HTTPStatus.OK)


class DopplerEventView(HomeAssistantView):
    """The bridge POSTs every state change and event here (PUT bridge/webhook),
    so entities update within a second instead of at the next poll."""

    requires_auth = False
    cors_allowed = True
    url = r"/api/sandman_doppler/event/{device_id}"
    name = "api:sandman_doppler:event"

    async def post(self, request: Request, device_id: str) -> Response:
        hass: HomeAssistant = request.app["hass"]
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get(device_id)
        if not device:
            _LOGGER.debug("Event for unknown device %s", device_id)
            return Response(status=HTTPStatus.OK)
        try:
            body = await request.json()
        except ValueError:
            return Response(status=HTTPStatus.BAD_REQUEST)
        dsn = body.get(ATTR_DSN)
        topic = body.get(ATTR_TOPIC)
        data = body.get(ATTR_DATA)
        if not dsn or not topic or (DOMAIN, dsn) not in device.identifiers:
            _LOGGER.debug("Event ignored (dsn %s, topic %s)", dsn, topic)
            return Response(status=HTTPStatus.OK)

        coordinator = next(
            (
                entry_data[dsn]
                for entry_data in hass.data.get(DOMAIN, {}).values()
                if isinstance(entry_data, dict) and dsn in entry_data
            ),
            None,
        )
        if coordinator is None:
            return Response(status=HTTPStatus.OK)

        event_data = {
            **(data if isinstance(data, dict) else {"value": data}),
            ATTR_DSN: dsn,
            ATTR_DOPPLER_NAME: device.name,
            ATTR_NAME: device.name_by_user or device.name,
            ATTR_DEVICE_ID: device_id,
        }
        if topic == "buttons":
            hass.bus.async_fire(EVENT_BUTTON_EVENT, event_data)
        elif topic == "alarms/event":
            hass.bus.async_fire(EVENT_ALARM_EVENT, event_data)
        elif topic == "voice/request":
            hass.bus.async_fire(EVENT_VOICE_REQUEST, event_data)
        else:
            coordinator.apply_bridge_update(topic, data)
        return Response(status=HTTPStatus.OK)
