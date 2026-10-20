"""Data coordinator for the WUD integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EVENT_CONNECTED, WudApiClient, WudAuthError, WudError
from .const import (
    DOMAIN,
    EVENT_CONTAINER_ADDED,
    EVENT_CONTAINER_REMOVED,
    EVENT_CONTAINER_UPDATED,
    EVENT_WATCH_STOP,
)
from .github import GithubReleases

_LOGGER = logging.getLogger(__name__)

EVENTS_RETRY_MIN = 5
EVENTS_RETRY_MAX = 300

type WudConfigEntry = ConfigEntry[WudCoordinator]
type ContainerEventListener = Callable[[str, dict[str, Any]], None]


def container_key(container: dict[str, Any]) -> str:
    """Stable key of a container (its id changes on every recreate)."""
    return f"{container.get('watcher')}/{container.get('name')}"


class WudCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls /api/containers and applies live changes from /api/events."""

    config_entry: WudConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: WudConfigEntry,
        api: WudApiClient,
        github: GithubReleases,
        scan_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=scan_interval,
        )
        self.api = api
        self.github = github
        self.version: str | None = None
        self.hub_device_id: str | None = None
        self._event_listeners: list[ContainerEventListener] = []
        self.events_connected = False

    async def _async_setup(self) -> None:
        try:
            app = await self.api.get_app()
        except WudError as err:
            raise UpdateFailed(str(err)) from err
        if isinstance(app, dict):
            self.version = app.get("version")

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            containers = await self.api.get_containers()
        except WudAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WudError as err:
            raise UpdateFailed(str(err)) from err
        return {container_key(c): c for c in containers if isinstance(c, dict)}

    @callback
    def async_add_event_listener(
        self, listener: ContainerEventListener
    ) -> CALLBACK_TYPE:
        """Subscribe to raw container events (type, container)."""
        self._event_listeners.append(listener)

        @callback
        def remove() -> None:
            self._event_listeners.remove(listener)

        return remove

    async def async_listen_events(self) -> None:
        """Consume the SSE stream forever, reconnecting on failure."""
        delay = EVENTS_RETRY_MIN
        while True:
            try:
                async for event in self.api.events():
                    if event.get("type") == EVENT_CONNECTED:
                        self.events_connected = True
                        delay = EVENTS_RETRY_MIN
                        _LOGGER.debug("Connected to WUD event stream")
                        # Resync what may have been missed while disconnected
                        await self.async_request_refresh()
                        continue
                    self._handle_event(event)
            except WudAuthError as err:
                _LOGGER.warning("WUD event stream unauthorized (%s)", err)
                delay = EVENTS_RETRY_MAX
            except WudError as err:
                _LOGGER.debug("WUD event stream error: %s", err)
            self.events_connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, EVENTS_RETRY_MAX)

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        container = event.get("data")
        if event_type == EVENT_WATCH_STOP:
            self.hass.async_create_task(self.async_request_refresh())
            return
        if event_type not in (
            EVENT_CONTAINER_ADDED,
            EVENT_CONTAINER_UPDATED,
            EVENT_CONTAINER_REMOVED,
        ) or not isinstance(container, dict):
            return

        for listener in list(self._event_listeners):
            listener(event_type, container)

        if self.data is None:
            return
        key = container_key(container)
        data = dict(self.data)
        if event_type == EVENT_CONTAINER_REMOVED:
            # During an update the replacement (same name, new id) can be
            # indexed before the old container is removed: keep it.
            current = data.get(key)
            if current is None or current.get("id") != container.get("id"):
                return
            data.pop(key)
        else:
            data[key] = container
        self.async_set_updated_data(data)
