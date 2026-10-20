"""Tests for setup, the coordinator and the event stream."""

from __future__ import annotations

import asyncio
import copy
import json
from unittest.mock import patch

import aiohttp
from homeassistant.components.update import ATTR_INSTALLED_VERSION
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wud import async_remove_config_entry_device
from custom_components.wud.const import CONF_SCAN_INTERVAL, DOMAIN

from .conftest import WudState
from .const import APP, EXPLORER, MATTER, MATTER_NEW_ID, URL

MATTER_ENTITY = "update.matterjs_server"


async def test_setup_and_unload(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    assert setup_integration.state is ConfigEntryState.LOADED
    assert len(hass.states.async_entity_ids("update")) == 3

    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert setup_integration.state is ConfigEntryState.NOT_LOADED


async def test_setup_retry_when_unreachable(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_events: None,
) -> None:
    aioclient_mock.get(f"{URL}/api/app", exc=aiohttp.ClientConnectionError())
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    no_events: None,
) -> None:
    aioclient_mock.get(f"{URL}/api/app", json=APP)
    aioclient_mock.get(f"{URL}/api/containers", status=401)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_stale_entities_removed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    wud: WudState,
    no_events: None,
) -> None:
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "update",
        DOMAIN,
        f"{config_entry.entry_id}_local/gone",
        config_entry=config_entry,
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(stale.entity_id) is None
    assert registry.async_get(MATTER_ENTITY) is not None


async def test_stale_devices_removed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    wud: WudState,
    no_events: None,
) -> None:
    devices = dr.async_get(hass)
    stale = devices.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{config_entry.entry_id}_local/gone")},
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert devices.async_get(stale.id) is None
    assert devices.async_get_device_by_identifier(
        (DOMAIN, config_entry.entry_id), config_entry.entry_id
    )


async def test_remove_config_entry_device(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    entry_id = setup_integration.entry_id
    devices = dr.async_get(hass)
    hub = devices.async_get_device_by_identifier((DOMAIN, entry_id), entry_id)
    matter = devices.async_get_device_by_identifier(
        (DOMAIN, f"{entry_id}_local/matterjs-server"), entry_id
    )
    gone = devices.async_get_or_create(
        config_entry_id=entry_id, identifiers={(DOMAIN, f"{entry_id}_local/gone")}
    )
    assert not await async_remove_config_entry_device(hass, setup_integration, hub)
    assert not await async_remove_config_entry_device(hass, setup_integration, matter)
    assert await async_remove_config_entry_device(hass, setup_integration, gone)


async def test_options_reload(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        setup_integration, options={CONF_SCAN_INTERVAL: 30}
    )
    await hass.async_block_till_done()
    assert setup_integration.state is ConfigEntryState.LOADED
    assert setup_integration.runtime_data.update_interval.total_seconds() == 1800


async def test_event_updates_entity(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    updated = copy.deepcopy(MATTER)
    updated.update(updateAvailable=False, updateKind={"kind": "unknown"})
    updated["image"]["tag"]["value"] = "1.5.0"
    coordinator._handle_event({"type": "wud:container-updated", "data": updated})
    await hass.async_block_till_done()
    state = hass.states.get(MATTER_ENTITY)
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_INSTALLED_VERSION] == "1.5.0"


async def test_event_adds_new_entity(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    new = copy.deepcopy(EXPLORER)
    new.update(id="e" * 64, name="grafana", displayName="grafana")
    aioclient_mock.get(f"{URL}/api/containers/{new['id']}/triggers", json=[])
    setup_integration.runtime_data._handle_event(
        {"type": "wud:container-added", "data": new}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("update.grafana") is not None


async def test_remove_of_replaced_container_is_ignored(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The replacement may be indexed before the old container is removed."""
    coordinator = setup_integration.runtime_data
    replacement = {**copy.deepcopy(MATTER), "id": MATTER_NEW_ID}
    coordinator._handle_event({"type": "wud:container-added", "data": replacement})
    coordinator._handle_event({"type": "wud:container-removed", "data": MATTER})
    assert coordinator.data["local/matterjs-server"]["id"] == MATTER_NEW_ID


async def test_unknown_events_ignored(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    before = coordinator.data
    coordinator._handle_event({"type": "wud:container-report", "data": MATTER})
    coordinator._handle_event({"type": "wud:container-updated", "data": "oops"})
    coordinator._handle_event({})
    assert coordinator.data is before


async def test_watch_stop_requests_refresh(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    with patch.object(coordinator, "async_request_refresh") as refresh:
        coordinator._handle_event({"type": "wud:watch-stop", "data": {}})
        await hass.async_block_till_done()
    refresh.assert_called_once()


class _Stop(Exception):
    """Break out of the reconnect loop."""


async def _setup_without_events(hass: HomeAssistant, entry: MockConfigEntry):
    with patch(
        "custom_components.wud.coordinator.WudCoordinator.async_listen_events",
        lambda self: asyncio.sleep(0),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    return entry.runtime_data


async def _run_events(coordinator, stop_after: int) -> list[float]:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == stop_after:
            raise _Stop

    with (
        patch("custom_components.wud.coordinator.asyncio.sleep", fake_sleep),
        pytest.raises(_Stop),
    ):
        await coordinator.async_listen_events()
    return delays


async def test_listen_events_applies_and_reconnects(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    wud: WudState,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    updated = {**copy.deepcopy(MATTER), "displayName": "Matter"}
    payload = (
        b": keepalive\n\n"
        + b'data: {"type": "wud:container-updated", "data": '
        + json.dumps(updated).encode()
        + b"}\n\n"
    )
    aioclient_mock.get(f"{URL}/api/events", content=payload)
    coordinator = await _setup_without_events(hass, config_entry)
    calls = aioclient_mock.call_count

    delays = await _run_events(coordinator, stop_after=2)
    await hass.async_block_till_done()

    assert coordinator.data["local/matterjs-server"]["displayName"] == "Matter"
    # Each successful connection resets the backoff
    assert delays == [5, 5]
    assert coordinator.events_connected is False
    # Every (re)connection resyncs the containers
    container_calls = [
        c for c in aioclient_mock.mock_calls[calls:] if c[1].path == "/api/containers"
    ]
    assert len(container_calls) >= 1


async def test_listen_events_failure_backoff(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    wud: WudState,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    aioclient_mock.get(f"{URL}/api/events", exc=aiohttp.ClientConnectionError())
    coordinator = await _setup_without_events(hass, config_entry)
    assert await _run_events(coordinator, stop_after=8) == [
        5,
        10,
        20,
        40,
        80,
        160,
        300,
        300,
    ]


async def test_listen_events_auth_error_backs_off(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    wud: WudState,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    aioclient_mock.get(f"{URL}/api/events", status=401)
    coordinator = await _setup_without_events(hass, config_entry)
    assert await _run_events(coordinator, stop_after=1) == [300]
