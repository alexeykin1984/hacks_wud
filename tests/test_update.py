"""Tests for the WUD update entities."""

from __future__ import annotations

import copy
from typing import Any

from homeassistant.components.update import (
    ATTR_IN_PROGRESS,
    ATTR_INSTALLED_VERSION,
    ATTR_LATEST_VERSION,
    ATTR_RELEASE_SUMMARY,
    ATTR_RELEASE_URL,
    ATTR_TITLE,
    ATTR_UPDATE_PERCENTAGE,
    DOMAIN as UPDATE_DOMAIN,
    SERVICE_INSTALL,
    UpdateEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_ENTITY_PICTURE,
    ATTR_ICON,
    ATTR_SUPPORTED_FEATURES,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.wud import update as wud_update
from custom_components.wud.const import DOMAIN
from custom_components.wud.update import (
    Stage,
    container_versions,
    resolve_icon,
    stage_progress,
)

from .conftest import WudState
from .const import (
    EXPLORER,
    GITHUB_RELEASE,
    MATTER,
    MATTER_ID,
    MATTER_NEW_ID,
    TRIGGER_COMPOSE,
    URL,
)

MATTER_ENTITY = "update.wud_matterjs_server"
EXPLORER_ENTITY = "update.wud_mqtt_explorer"
PORTAINER_ENTITY = "update.wud_portainer_ce"
TRIGGER_URL = f"{URL}/api/containers/{MATTER_ID}/triggers/dockercompose/app"


# ----------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("icon", "expected"),
    [
        (None, ("mdi:docker", None)),
        ("", ("mdi:docker", None)),
        ("mdi:home-assistant", ("mdi:home-assistant", None)),
        ("mdi-docker", ("mdi:docker", None)),
        ("si:portainer", (None, "https://cdn.simpleicons.org/portainer")),
        ("si-grafana", (None, "https://cdn.simpleicons.org/grafana")),
        (
            "sh:nextcloud",
            (None, "https://cdn.jsdelivr.net/gh/selfhst/icons/png/nextcloud.png"),
        ),
        (
            "selfhst:plex",
            (None, "https://cdn.jsdelivr.net/gh/selfhst/icons/png/plex.png"),
        ),
        (
            "hl-jellyfin",
            (None, "https://cdn.jsdelivr.net/gh/selfhst/icons/png/jellyfin.png"),
        ),
        ("fab:docker", (None, "https://api.iconify.design/fa6-brands/docker.svg")),
        ("fa-server", (None, "https://api.iconify.design/fa6-solid/server.svg")),
        ("far:bell", (None, "https://api.iconify.design/fa6-regular/bell.svg")),
        ("logos:redis", (None, "https://api.iconify.design/logos/redis.svg")),
        ("https://x.io/i.png", (None, "https://x.io/i.png")),
        ("traefik", (None, "https://cdn.simpleicons.org/traefik")),
    ],
)
def test_resolve_icon(icon: Any, expected: tuple[str | None, str | None]) -> None:
    assert resolve_icon(icon) == expected


def test_container_versions() -> None:
    assert container_versions(MATTER) == ("1.4.0", "1.5.0")
    assert container_versions(EXPLORER) == ("latest", "latest")
    digest = copy.deepcopy(EXPLORER)
    digest["updateAvailable"] = True
    digest["updateKind"] = {
        "kind": "digest",
        "localValue": "sha256:1111111111111111aaaa",
        "remoteValue": "sha256:2222222222222222bbbb",
    }
    assert container_versions(digest) == (
        "latest (111111111111)",
        "latest (222222222222)",
    )
    digest["result"]["version"] = "4.3.0"
    assert container_versions(digest)[1] == "4.3.0 (222222222222)"


def test_stage_progress_monotonic_within_bounds() -> None:
    assert stage_progress(Stage.PULLING, 0) == 5
    values = [stage_progress(Stage.PULLING, t) for t in range(0, 600, 10)]
    assert values == sorted(values)
    assert values[-1] < 60
    assert stage_progress(Stage.DONE, 0) == 100
    # A stage never reaches the start of the next one
    order = list(wud_update.STAGES)
    for stage, nxt in zip(order, order[1:], strict=False):
        assert stage_progress(stage, 10_000) <= wud_update.STAGES[nxt][0]


# ---------------------------------------------------------------- entities


async def test_entities(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    state = hass.states.get(MATTER_ENTITY)
    assert state.state == STATE_ON
    attrs = state.attributes
    assert attrs[ATTR_INSTALLED_VERSION] == "1.4.0"
    assert attrs[ATTR_LATEST_VERSION] == "1.5.0"
    assert attrs[ATTR_TITLE] == "matterjs-server"
    assert attrs[ATTR_ICON] == "mdi:docker"
    assert attrs.get(ATTR_ENTITY_PICTURE) is None
    assert attrs[ATTR_SUPPORTED_FEATURES] == (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )
    assert attrs[ATTR_RELEASE_SUMMARY] == GITHUB_RELEASE["body"]
    assert attrs[ATTR_RELEASE_URL] == GITHUB_RELEASE["html_url"]
    assert attrs[ATTR_IN_PROGRESS] is False
    assert attrs["trigger"] == "dockercompose.app"
    assert attrs["semver_diff"] == "minor"
    assert attrs["stack"] == "dockers"
    assert attrs["image"] == "matter-js/matterjs-server"

    state = hass.states.get(EXPLORER_ENTITY)
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_INSTALLED_VERSION] == "latest"
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )

    # Display name, simple-icons picture, no update trigger, release link
    state = hass.states.get(PORTAINER_ENTITY)
    assert state.attributes[ATTR_TITLE] == "Portainer CE"
    assert (
        state.attributes[ATTR_ENTITY_PICTURE] == "https://cdn.simpleicons.org/portainer"
    )
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == 0
    assert state.attributes[ATTR_RELEASE_URL].endswith("/releases/2.45.2")
    assert state.attributes["error"] == "registry timeout"
    assert "trigger" not in state.attributes


async def test_device(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), setup_integration.entry_id
    )
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, setup_integration.entry_id)}
    assert device.name == "WUD"
    assert device.sw_version == "9.3.0"
    assert device.configuration_url == URL


async def test_release_notes(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "update/release_notes", "entity_id": MATTER_ENTITY}
    )
    result = await client.receive_json()
    assert result["success"]
    assert result["result"] == (
        f"{GITHUB_RELEASE['body']}\n\n[v1.5.0 on GitHub]({GITHUB_RELEASE['html_url']})"
    )


# ----------------------------------------------------------------- install


def _record(hass: HomeAssistant, entity_id: str) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []

    def listener(event: Event) -> None:
        new = event.data["new_state"]
        if new is not None and event.data["entity_id"] == entity_id:
            states.append(
                {
                    "state": new.state,
                    "in_progress": new.attributes.get(ATTR_IN_PROGRESS),
                    "percentage": new.attributes.get(ATTR_UPDATE_PERCENTAGE),
                    "stage": new.attributes.get("update_stage"),
                }
            )

    hass.bus.async_listen("state_changed", listener)
    return states


async def test_install_progress_stages(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    wud: WudState,
) -> None:
    coordinator = setup_integration.runtime_data
    old = copy.deepcopy(MATTER)
    new = copy.deepcopy(MATTER)
    new.update(
        id=MATTER_NEW_ID,
        status="created",
        updateAvailable=False,
        updateKind={"kind": "unknown"},
    )
    new["image"]["tag"]["value"] = "1.5.0"
    states = _record(hass, MATTER_ENTITY)

    async def run_trigger(method, url, data):
        # What WUD streams while dockercompose recreates the container
        for event_type, container in (
            ("wud:container-updated", {**old, "status": "exited"}),
            ("wud:container-removed", old),
            ("wud:container-added", new),
            ("wud:container-updated", {**new, "status": "running"}),
        ):
            coordinator._handle_event({"type": event_type, "data": container})
        wud.containers[0] = {**new, "status": "running"}
        return AiohttpClientMockResponse(method, url, json={})

    aioclient_mock.post(TRIGGER_URL, side_effect=run_trigger)
    aioclient_mock.get(
        f"{URL}/api/containers/{MATTER_NEW_ID}/triggers", json=[TRIGGER_COMPOSE]
    )

    await hass.services.async_call(
        UPDATE_DOMAIN, SERVICE_INSTALL, {ATTR_ENTITY_ID: MATTER_ENTITY}, blocking=True
    )

    # Each stage is written once or more (stage change + coordinator update)
    stages = list(dict.fromkeys(s["stage"] for s in states if s["stage"]))
    assert stages == [
        "starting",
        "pulling",
        "stopped",
        "removed",
        "created",
        "started",
        "finalizing",
        "done",
    ]
    percentages = [s["percentage"] for s in states if s["in_progress"]]
    assert percentages == sorted(percentages)
    assert percentages[-1] == 100
    assert {s["state"] for s in states if s["in_progress"]} >= {STATE_ON}

    state = hass.states.get(MATTER_ENTITY)
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_IN_PROGRESS] is False
    assert state.attributes[ATTR_UPDATE_PERCENTAGE] is None
    assert state.attributes[ATTR_INSTALLED_VERSION] == "1.5.0"
    assert state.attributes["container_id"] == MATTER_NEW_ID
    assert "update_stage" not in state.attributes


async def test_install_failure(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    aioclient_mock.post(
        TRIGGER_URL,
        status=500,
        json={"error": "Trigger execution failed", "message": "pull access denied"},
    )
    with pytest.raises(HomeAssistantError, match="pull access denied"):
        await hass.services.async_call(
            UPDATE_DOMAIN,
            SERVICE_INSTALL,
            {ATTR_ENTITY_ID: MATTER_ENTITY},
            blocking=True,
        )
    state = hass.states.get(MATTER_ENTITY)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_IN_PROGRESS] is False


async def test_install_without_trigger(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    wud: WudState,
) -> None:
    """Install raises when the trigger association disappeared."""
    entity = hass.data["entity_components"][UPDATE_DOMAIN].get_entity(MATTER_ENTITY)
    entity._trigger = None
    entity._trigger_container_id = None
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/api/containers/{MATTER_ID}/triggers", json=[])
    with pytest.raises(HomeAssistantError, match="No update trigger"):
        await entity.async_install(None, False)
    assert not aioclient_mock.mock_calls[-1][1].path.endswith("/app")


async def test_trigger_preference(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """docker / dockercompose win over command, notification triggers ignored."""
    entity = hass.data["entity_components"][UPDATE_DOMAIN].get_entity(MATTER_ENTITY)
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{URL}/api/containers/{MATTER_ID}/triggers",
        json=[
            {"type": "command", "name": "a"},
            {"type": "slack", "name": "b"},
            {"type": "docker", "name": "z"},
            {"type": "docker", "name": "local"},
        ],
    )
    await entity._lookup_trigger(MATTER_ID)
    assert entity._trigger == {"type": "docker", "name": "local"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{URL}/api/containers/{MATTER_ID}/triggers",
        json=[{"type": "nomad", "name": "n"}, {"type": "command", "name": "c"}],
    )
    await entity._lookup_trigger(MATTER_ID)
    assert entity._trigger == {"type": "command", "name": "c"}


async def test_unavailable_when_container_removed(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    coordinator = setup_integration.runtime_data
    coordinator._handle_event({"type": "wud:container-removed", "data": EXPLORER})
    await hass.async_block_till_done()
    assert hass.states.get(EXPLORER_ENTITY).state == STATE_UNAVAILABLE
