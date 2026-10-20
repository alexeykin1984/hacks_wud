"""Tests for the WUD API client."""

from __future__ import annotations

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wud.api import (
    EVENT_CONNECTED,
    WudApiClient,
    WudAuthError,
    WudConnectionError,
    WudError,
)

from .const import MATTER_ID, URL


def _client(hass: HomeAssistant, **kwargs) -> WudApiClient:
    return WudApiClient(async_get_clientsession(hass), f"{URL}/", **kwargs)


async def test_strips_trailing_slash(hass: HomeAssistant) -> None:
    assert _client(hass).url == URL


async def test_get_containers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{URL}/api/containers", json=[{"id": "1"}])
    assert await _client(hass, username="u", password="p").get_containers() == [
        {"id": "1"}
    ]


async def test_basic_auth_header(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{URL}/api/app", json={"name": "wud"})
    await _client(hass, username="admin", password="admin").get_app()
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == "Basic YWRtaW46YWRtaW4="


async def test_no_auth_header(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{URL}/api/app", json={"name": "wud"})
    await _client(hass).get_app()
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert "Authorization" not in headers


async def test_bearer_token_header(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{URL}/api/app", json={"name": "wud"})
    await _client(hass, token="tok").get_app()
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == "Bearer tok"


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    aioclient_mock.get(f"{URL}/api/containers", status=status)
    with pytest.raises(WudAuthError):
        await _client(hass).get_containers()


async def test_error_message_from_body(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        f"{URL}/api/containers/{MATTER_ID}/triggers/dockercompose/app",
        status=500,
        json={"error": "Trigger execution failed", "message": "pull failed"},
    )
    with pytest.raises(WudError, match="pull failed"):
        await _client(hass).run_container_trigger(MATTER_ID, "dockercompose", "app")


async def test_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{URL}/api/app", exc=aiohttp.ClientConnectionError("down"))
    with pytest.raises(WudConnectionError):
        await _client(hass).get_app()


async def test_timeout(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{URL}/api/app", exc=TimeoutError())
    with pytest.raises(WudConnectionError):
        await _client(hass).get_app()


async def test_empty_body(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{URL}/api/containers/x/triggers/docker/local", text="")
    assert await _client(hass).run_container_trigger("x", "docker", "local") is None


async def test_events_parsing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    stream = (
        b": keepalive\n\n"
        b'data: {"type": "wud:container-updated", "data": {"name": "a"}}\n\n'
        b"data: {not json}\n\n"
        b'data: {"type": "wud:container-added",\n'
        b'data:  "data": {"name": "b"}}\r\n\r\n'
    )
    aioclient_mock.get(f"{URL}/api/events", content=stream)
    events = [event async for event in _client(hass).events()]
    assert events == [
        {"type": EVENT_CONNECTED},
        {"type": "wud:container-updated", "data": {"name": "a"}},
        {"type": "wud:container-added", "data": {"name": "b"}},
    ]


async def test_events_unauthorized(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{URL}/api/events", status=401)
    with pytest.raises(WudAuthError):
        async for _ in _client(hass).events():
            pass


async def test_events_server_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{URL}/api/events", status=502)
    with pytest.raises(WudError):
        async for _ in _client(hass).events():
            pass
