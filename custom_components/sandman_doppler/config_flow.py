"""Adds config flow for Doppler (local-only)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_LOCAL_KEY, CONF_DSN

_LOGGER = logging.getLogger(__name__)


class DopplerFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Doppler clocks (local-only)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, str] = None) -> FlowResult:
        """Handle a flow initialized by the user."""
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_DSN])
            self._abort_if_unique_id_configured()

            if await self._connection_valid(
                user_input[CONF_HOST],
                int(user_input[CONF_PORT]),
                user_input[CONF_LOCAL_KEY],
                user_input[CONF_DSN],
            ):
                return self.async_create_entry(
                    title=f"Doppler ({user_input[CONF_DSN]})",
                    data={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: int(user_input[CONF_PORT]),
                        CONF_LOCAL_KEY: user_input[CONF_LOCAL_KEY],
                        CONF_DSN: user_input[CONF_DSN],
                    },
                )
            else:
                errors["base"] = "cannot_connect"

        user_input = user_input or {}

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=user_input.get(CONF_HOST, "")): cv.string,
                    vol.Required(
                        CONF_PORT, default=user_input.get(CONF_PORT, 443)
                    ): vol.Coerce(int),
                    vol.Required(
                        CONF_LOCAL_KEY, default=user_input.get(CONF_LOCAL_KEY, "")
                    ): cv.string,
                    vol.Required(
                        CONF_DSN, default=user_input.get(CONF_DSN, "")
                    ): cv.string,
                }
            ),
            errors=errors,
        )

    async def _connection_valid(
        self,
        host: str,
        port: int,
        local_key: str,
        dsn: str,
    ) -> bool:
        """Validate connection to the local Doppler device.

        Mirrors the nonce-based auth from doppyler lib:
        1. GET /{dsn}/nonce → nonce string
        2. SHA256(nonce + local_key) → base64 digest
        3. Bearer token = "{nonce}|{base64_digest}"
        4. Test with GET /{dsn}/hardware/volume
        """
        session = async_get_clientsession(self.hass)
        base_url = f"https://{host}:{port}"

        try:
            # Step 1: Fetch nonce
            async with session.get(
                f"{base_url}/{dsn}/nonce",
                ssl=False,
                timeout=aiohttp.ClientTimeout(10),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error("Nonce fetch failed: HTTP %s", resp.status)
                    return False
                # Handle both JSON {"nonce":"..."} and plain text responses
                content_type = resp.headers.get("Content-Type", "")
                if "json" in content_type:
                    nonce_data = await resp.json()
                    nonce = nonce_data.get("nonce", "")
                else:
                    nonce = await resp.text()
                nonce = nonce.strip()
                if not nonce:
                    _LOGGER.error("Empty nonce in response")
                    return False

            # Step 2: Compute auth token
            m = hashlib.sha256()
            m.update(nonce.encode("ascii"))
            m.update(local_key.encode("ascii"))
            final_key = f"{nonce}|{base64.b64encode(m.digest()).decode('ascii')}"

            # Step 3: Test with an authenticated request
            async with session.get(
                f"{base_url}/{dsn}/hardware/volume",
                headers={"Authorization": f"Bearer {final_key}"},
                ssl=False,
                timeout=aiohttp.ClientTimeout(10),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error(
                        "Auth test failed: HTTP %s (invalid local_key?)",
                        resp.status,
                    )
                    return False

            _LOGGER.info(
                "Successfully connected to Doppler %s at %s:%s",
                dsn, host, port,
            )
            return True

        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            _LOGGER.debug(
                "Connection to Doppler at %s:%s failed: %s",
                host, port, exc,
            )
            return False
