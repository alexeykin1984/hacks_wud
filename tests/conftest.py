"""Fixtures for the WUD integration tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.wud.const import DOMAIN

from .const import (
    APP,
    CONFIG_DATA,
    EXPLORER,
    GITHUB_RELEASE,
    MATTER,
    PORTAINER,
    TRIGGER_COMPOSE,
    TRIGGER_MQTT,
    URL,
    containers,
)

GITHUB_RELEASES = "https://api.github.com/repos/matter-js/matterjs-server/releases/tags"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom_components in every test."""


@pytest.fixture
def no_events() -> Generator[None]:
    """Do not consume the SSE stream (tested on its own)."""

    async def idle(self) -> None:
        return None

    with patch(
        "custom_components.wud.coordinator.WudCoordinator.async_listen_events", idle
    ):
        yield


class WudState:
    """Mutable server state served by the mocked API."""

    def __init__(self) -> None:
        self.containers: list[dict[str, Any]] = containers()


@pytest.fixture
def wud(aioclient_mock: AiohttpClientMocker) -> WudState:
    """Mock a WUD server."""
    state = WudState()

    async def list_containers(method, url, data):
        return AiohttpClientMockResponse(method, url, json=state.containers)

    aioclient_mock.get(f"{URL}/api/app", json=APP)
    aioclient_mock.get(f"{URL}/api/containers", side_effect=list_containers)
    aioclient_mock.get(
        f"{URL}/api/containers/{MATTER['id']}/triggers",
        json=[TRIGGER_COMPOSE, TRIGGER_MQTT],
    )
    aioclient_mock.get(
        f"{URL}/api/containers/{EXPLORER['id']}/triggers", json=[TRIGGER_COMPOSE]
    )
    aioclient_mock.get(f"{GITHUB_RELEASES}/1.5.0", status=404)
    aioclient_mock.get(f"{GITHUB_RELEASES}/v1.5.0", json=GITHUB_RELEASE)
    # Portainer is only associated with a notification trigger
    aioclient_mock.get(
        f"{URL}/api/containers/{PORTAINER['id']}/triggers", json=[TRIGGER_MQTT]
    )
    return state


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A WUD config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="WUD", data=CONFIG_DATA, unique_id=URL.lower()
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, wud: WudState, no_events: None
) -> MockConfigEntry:
    """Set up the integration against the mocked server."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return config_entry
