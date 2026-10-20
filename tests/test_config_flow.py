"""Tests for the WUD config flow."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wud.const import CONF_GITHUB_TOKEN, CONF_SCAN_INTERVAL, DOMAIN

from .const import APP, CONFIG_DATA, URL

USER_INPUT = {**CONFIG_DATA, "url": f" {URL}/ "}


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    with patch("custom_components.wud.async_setup_entry", return_value=True) as mock:
        yield mock


def _mock_server(aioclient_mock: AiohttpClientMocker, **containers_kwargs) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/api/app", json=APP)
    aioclient_mock.get(f"{URL}/api/containers", **(containers_kwargs or {"json": []}))


async def test_user_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "WUD"
    assert result["data"] == CONFIG_DATA
    assert result["result"].unique_id == URL.lower()


@pytest.mark.parametrize(
    ("app", "containers", "error"),
    [
        ({"json": APP}, {"status": 401}, "invalid_auth"),
        ({"exc": aiohttp.ClientConnectionError()}, {"json": []}, "cannot_connect"),
        ({"json": {"name": "nginx"}}, {"json": []}, "not_wud"),
        ({"text": "<html>"}, {"json": []}, "not_wud"),
        ({"exc": ValueError("boom")}, {"json": []}, "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    app: dict,
    containers: dict,
    error: str,
) -> None:
    aioclient_mock.get(f"{URL}/api/app", **app)
    aioclient_mock.get(f"{URL}/api/containers", **containers)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # Recovers once the server answers properly
    _mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    _mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_second_server_title_has_host(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    other = "http://10.0.0.2:3002"
    aioclient_mock.get(f"{other}/api/app", json=APP)
    aioclient_mock.get(f"{other}/api/containers", json=[])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CONFIG_DATA, "url": other}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "WUD 10.0.0.2:3002"


async def test_reconfigure(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    _mock_server(aioclient_mock)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    new_data = {**CONFIG_DATA, "username": "", "password": "", "api_token": "tok"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new_data)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data["api_token"] == "tok"


async def test_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    _mock_server(aioclient_mock, status=401)
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CONFIG_DATA, "password": "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    _mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CONFIG_DATA, "password": "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["password"] == "new"


async def test_options_flow(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 15, CONF_GITHUB_TOKEN: "gh"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_SCAN_INTERVAL: 15, CONF_GITHUB_TOKEN: "gh"}
