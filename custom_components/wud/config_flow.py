"""Config flow for the WUD (What's Up Docker) integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
from urllib.parse import urlparse

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_API_TOKEN,
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import WudApiClient, WudAuthError, WudConnectionError, WudError
from .const import (
    CONF_GITHUB_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _user_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_URL, default=defaults.get(CONF_URL, "http://")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
            vol.Optional(
                CONF_USERNAME,
                description={"suggested_value": defaults.get(CONF_USERNAME)},
            ): str,
            vol.Optional(
                CONF_PASSWORD,
                description={"suggested_value": defaults.get(CONF_PASSWORD)},
            ): PASSWORD_SELECTOR,
            vol.Optional(
                CONF_API_TOKEN,
                description={"suggested_value": defaults.get(CONF_API_TOKEN)},
            ): PASSWORD_SELECTOR,
            vol.Optional(
                CONF_VERIFY_SSL,
                default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): bool,
        }
    )


async def _validate(hass, data: Mapping[str, Any]) -> dict[str, Any]:
    verify_ssl = data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    api = WudApiClient(
        async_get_clientsession(hass, verify_ssl=verify_ssl),
        data[CONF_URL],
        username=data.get(CONF_USERNAME),
        password=data.get(CONF_PASSWORD),
        token=data.get(CONF_API_TOKEN),
        verify_ssl=verify_ssl,
    )
    app = await api.get_app()
    if not isinstance(app, dict) or app.get("name") != "wud":
        raise WudError("Not a WUD server")
    # /api/app is public: check the credentials on a protected endpoint
    await api.get_containers()
    return app


class WudConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for WUD."""

    VERSION = 1

    async def _async_try(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> bool:
        user_input[CONF_URL] = user_input[CONF_URL].strip().rstrip("/")
        try:
            await _validate(self.hass, user_input)
        except WudAuthError:
            errors["base"] = "invalid_auth"
        except WudConnectionError:
            errors["base"] = "cannot_connect"
        except WudError:
            errors["base"] = "not_wud"
        except Exception:
            _LOGGER.exception("Unexpected error validating WUD")
            errors["base"] = "unknown"
        return not errors

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and await self._async_try(user_input, errors):
            await self.async_set_unique_id(user_input[CONF_URL].lower())
            self._abort_if_unique_id_configured()
            title = "WUD"
            if self._async_current_entries():
                host = urlparse(user_input[CONF_URL]).netloc or user_input[CONF_URL]
                title = f"WUD {host}"
            return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reconfigure()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = (
            self._get_reauth_entry()
            if self.source == "reauth"
            else self._get_reconfigure_entry()
        )
        errors: dict[str, str] = {}
        if user_input is not None and await self._async_try(user_input, errors):
            return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_user_schema(user_input or entry.data),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return WudOptionsFlow()


class WudOptionsFlow(OptionsFlow):
    """WUD options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=1440,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    ),
                    vol.Optional(
                        CONF_GITHUB_TOKEN,
                        description={"suggested_value": options.get(CONF_GITHUB_TOKEN)},
                    ): PASSWORD_SELECTOR,
                }
            ),
        )
