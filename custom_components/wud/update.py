"""Update entities for containers watched by WUD."""

from __future__ import annotations

import asyncio
from enum import StrEnum
import logging
import math
import time
from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import WudError
from .const import (
    DEFAULT_ICON,
    DOMAIN,
    EVENT_CONTAINER_ADDED,
    EVENT_CONTAINER_REMOVED,
    EVENT_CONTAINER_UPDATED,
    PREFERRED_TRIGGER_TYPES,
    UPDATE_TRIGGER_TYPES,
)
from .coordinator import WudConfigEntry, WudCoordinator, container_key
from .github import release_tag, resolve_github_repository

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

PROGRESS_TICK = 2  # seconds
SETTLE_TIMEOUT = 45  # seconds to wait for WUD to index the new container
SETTLE_POLL = 3


class Stage(StrEnum):
    """Update stages, as observed through the WUD event stream."""

    STARTING = "starting"
    PULLING = "pulling"
    STOPPED = "stopped"
    REMOVED = "removed"
    CREATED = "created"
    STARTED = "started"
    FINALIZING = "finalizing"
    DONE = "done"


# stage -> (percentage reached when entering the stage, ceiling the
# progress creeps toward while waiting for the next stage, creep time constant)
STAGES: dict[Stage, tuple[int, int, float]] = {
    Stage.STARTING: (0, 5, 2),
    Stage.PULLING: (5, 60, 45),
    Stage.STOPPED: (65, 74, 5),
    Stage.REMOVED: (75, 84, 5),
    Stage.CREATED: (85, 94, 5),
    Stage.STARTED: (95, 98, 5),
    Stage.FINALIZING: (98, 99, 10),
    Stage.DONE: (100, 100, 1),
}
_ORDER = list(STAGES)


def stage_progress(stage: Stage, elapsed: float) -> int:
    """Percentage after spending `elapsed` seconds in `stage`."""
    floor, ceiling, tau = STAGES[stage]
    return int(floor + (ceiling - floor) * (1 - math.exp(-max(elapsed, 0) / tau)))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WudConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up WUD update entities."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    # Drop entities of containers that no longer exist in WUD
    registry = er.async_get(hass)
    current = {_unique_id(entry.entry_id, key) for key in coordinator.data}
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain == "update" and reg_entry.unique_id not in current:
            registry.async_remove(reg_entry.entity_id)

    @callback
    def add_new() -> None:
        new = [key for key in coordinator.data if key not in known]
        if not new:
            return
        known.update(new)
        async_add_entities(WudUpdateEntity(coordinator, key) for key in new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


def _unique_id(entry_id: str, key: str) -> str:
    return f"{entry_id}_{key}"


def _short_digest(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.split(":", 1)[-1][:12]


def resolve_icon(display_icon: Any) -> tuple[str | None, str | None]:
    """Map a WUD displayIcon to an HA (icon, entity_picture) pair.

    Same conventions as the WUD UI (ui/src/components/IconRenderer.vue).
    """
    if not isinstance(display_icon, str) or not display_icon.strip():
        return DEFAULT_ICON, None
    raw = display_icon.strip()
    lower = raw.lower()
    if lower.startswith(("http://", "https://", "data:image/")):
        return None, raw

    def split(prefixes: tuple[str, ...]) -> str | None:
        for prefix in prefixes:
            for sep in (":", "-", " "):
                if lower.startswith(prefix + sep):
                    return lower[len(prefix) + 1 :]
        return None

    if name := split(("mdi",)):
        return f"mdi:{name}", None
    if name := split(("selfhst", "sh", "hl")):
        return None, f"https://cdn.jsdelivr.net/gh/selfhst/icons/png/{name}.png"
    if name := split(("simple-icons", "si")):
        return None, f"https://cdn.simpleicons.org/{name}"
    for prefixes, collection in (
        (("fab",), "fa6-brands"),
        (("far",), "fa6-regular"),
        (("fas", "fa"), "fa6-solid"),
    ):
        if name := split(prefixes):
            return None, f"https://api.iconify.design/{collection}/{name}.svg"
    if ":" in lower:
        collection, name = lower.split(":", 1)
        return None, f"https://api.iconify.design/{collection}/{name}.svg"
    return None, f"https://cdn.simpleicons.org/{lower}"


def container_versions(container: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (installed_version, latest_version) of a container."""
    image = container.get("image") or {}
    tag = (image.get("tag") or {}).get("value")
    update_kind = container.get("updateKind") or {}
    kind = update_kind.get("kind")
    local = update_kind.get("localValue")
    remote = update_kind.get("remoteValue")
    if container.get("updateAvailable") and kind == "tag" and local and remote:
        return local, remote
    if container.get("updateAvailable") and kind == "digest":
        result = container.get("result") or {}
        local_digest = _short_digest(local or (image.get("digest") or {}).get("value"))
        remote_digest = _short_digest(remote or result.get("digest"))
        installed = f"{tag} ({local_digest})" if local_digest else tag
        latest_label = result.get("version") or tag
        latest = f"{latest_label} ({remote_digest})" if remote_digest else latest_label
        return installed, latest
    return tag, tag


class WudUpdateEntity(CoordinatorEntity[WudCoordinator], UpdateEntity):
    """Update entity of one watched container."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WudCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        entry = coordinator.config_entry
        self._attr_unique_id = _unique_id(entry.entry_id, key)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="getwud",
            model="What's Up Docker",
            sw_version=coordinator.version,
            configuration_url=coordinator.api.url,
        )
        self._trigger: dict[str, Any] | None = None
        self._trigger_container_id: str | None = None
        self._trigger_lookup: asyncio.Task | None = None
        self._release_lookup: asyncio.Task | None = None

        # Install progress
        self._installing = False
        self._stage: Stage | None = None
        self._stage_since = 0.0
        self._percentage = 0
        self._old_id: str | None = None
        # Last known payload, kept while the container is being replaced
        self._last_container: dict[str, Any] | None = None
        self._ticker: asyncio.Task | None = None

        self._sync_attrs()

    # ----------------------------------------------------------------- data

    @property
    def container(self) -> dict[str, Any] | None:
        """Current WUD container payload."""
        container = (self.coordinator.data or {}).get(self._key)
        if container is None and self._installing:
            # The old container is removed before its replacement is indexed
            return self._last_container
        return container

    @property
    def entity_picture(self) -> str | None:
        # UpdateEntity defaults to the integration brand icon; use the
        # container's own picture, or none so that its mdi icon is shown.
        return self._attr_entity_picture

    @property
    def available(self) -> bool:
        return super().available and self.container is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync_attrs()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_event_listener(self._on_container_event)
        )
        self._schedule_lookups()

    async def async_will_remove_from_hass(self) -> None:
        for task in (self._ticker, self._trigger_lookup, self._release_lookup):
            if task and not task.done():
                task.cancel()
        await super().async_will_remove_from_hass()

    @callback
    def _sync_attrs(self) -> None:
        container = self.container
        if container is None:
            return
        self._last_container = container
        display_name = container.get("displayName") or container.get("name")
        self._attr_name = display_name
        self._attr_title = display_name
        self._attr_icon, self._attr_entity_picture = resolve_icon(
            container.get("displayIcon")
        )
        installed, latest = container_versions(container)
        self._attr_installed_version = installed
        self._attr_latest_version = latest
        self._schedule_lookups()

        features = UpdateEntityFeature(0)
        if self._has_release_target(container):
            features |= UpdateEntityFeature.RELEASE_NOTES
        if self._trigger:
            features |= UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
        self._attr_supported_features = features

        result = container.get("result") or {}
        release = self.coordinator.github.cached(container)
        self._attr_release_url = (
            result.get("link")
            or container.get("link")
            or (release.url if release else None)
        )
        self._attr_release_summary = release.summary if release else None
        self._attr_in_progress = self._installing
        self._attr_update_percentage = self._percentage if self._installing else None

    @staticmethod
    def _has_release_target(container: dict[str, Any]) -> bool:
        return bool(release_tag(container) and resolve_github_repository(container))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        container = self.container
        if container is None:
            return None
        image = container.get("image") or {}
        update_kind = container.get("updateKind") or {}
        attrs: dict[str, Any] = {
            "container_id": container.get("id"),
            "container_name": container.get("name"),
            "watcher": container.get("watcher"),
            "stack": container.get("stack")
            or (container.get("labels") or {}).get("com.docker.compose.project"),
            "status": container.get("status"),
            "image": image.get("name"),
            "registry": (image.get("registry") or {}).get("name"),
            "update_kind": update_kind.get("kind"),
            "semver_diff": update_kind.get("semverDiff"),
            "image_created": image.get("created"),
            "result_created": (container.get("result") or {}).get("created"),
            "snoozed": container.get("isSnoozed"),
            "cooling_down": container.get("isCoolingDown"),
            "error": (container.get("error") or {}).get("message"),
            "trigger": f"{self._trigger['type']}.{self._trigger['name']}"
            if self._trigger
            else None,
        }
        if self._installing and self._stage:
            attrs["update_stage"] = self._stage.value
        return {k: v for k, v in attrs.items() if v is not None}

    # -------------------------------------------------- background lookups

    @callback
    def _schedule_lookups(self) -> None:
        if self.hass is None:
            return
        container = self.container
        if container is None:
            return
        if self._trigger_container_id != container.get("id") and (
            self._trigger_lookup is None or self._trigger_lookup.done()
        ):
            self._trigger_lookup = self.hass.async_create_background_task(
                self._lookup_trigger(container["id"]), f"wud trigger {self._key}"
            )
        if (
            container.get("updateAvailable")
            and self._has_release_target(container)
            and self.coordinator.github.cached(container) is None
            and (self._release_lookup is None or self._release_lookup.done())
        ):
            self._release_lookup = self.hass.async_create_background_task(
                self._lookup_release(container), f"wud release {self._key}"
            )

    async def _lookup_trigger(self, container_id: str) -> None:
        try:
            triggers = await self.coordinator.api.get_container_triggers(container_id)
        except WudError as err:
            _LOGGER.debug("Unable to get triggers of %s: %s", self._key, err)
            return
        candidates = sorted(
            (t for t in triggers if t.get("type") in UPDATE_TRIGGER_TYPES),
            key=lambda t: (t.get("type", ""), t.get("name", "")),
        )
        trigger = next(
            (t for t in candidates if t.get("type") in PREFERRED_TRIGGER_TYPES),
            candidates[0] if candidates else None,
        )
        self._trigger = trigger
        self._trigger_container_id = container_id
        self._sync_attrs()
        self.async_write_ha_state()

    async def _lookup_release(self, container: dict[str, Any]) -> None:
        release = await self.coordinator.github.get(container)
        if release is not None and self.container is not None:
            self._sync_attrs()
            self.async_write_ha_state()

    async def async_release_notes(self) -> str | None:
        container = self.container
        if container is None:
            return None
        release = await self.coordinator.github.get(container)
        if release is None:
            return None
        notes = release.body or release.name
        if notes and release.url:
            notes = f"{notes}\n\n[{release.tag} on GitHub]({release.url})"
        return notes

    # -------------------------------------------------------------- install

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        container = self.container
        if container is None:
            raise HomeAssistantError(f"Container {self._key} is not known by WUD")
        if self._installing:
            raise HomeAssistantError(f"{self._key} is already being updated")
        if not self._trigger or self._trigger_container_id != container["id"]:
            await self._lookup_trigger(container["id"])
        trigger = self._trigger
        if not trigger:
            raise HomeAssistantError(
                f"No update trigger ({', '.join(UPDATE_TRIGGER_TYPES)}) "
                f"is associated with container {container.get('name')} in WUD"
            )

        self._installing = True
        self._old_id = container["id"]
        self._percentage = 0
        self._set_stage(Stage.STARTING)
        self._ticker = self.hass.async_create_background_task(
            self._tick(), f"wud progress {self._key}"
        )
        try:
            self._set_stage(Stage.PULLING)
            await self.coordinator.api.run_container_trigger(
                container["id"], trigger["type"], trigger["name"]
            )
            self._set_stage(Stage.FINALIZING)
            await self._wait_settled()
            self._set_stage(Stage.DONE)
        except WudError as err:
            raise HomeAssistantError(
                f"Update of {container.get('name')} with trigger "
                f"{trigger['type']}.{trigger['name']} failed: {err}"
            ) from err
        finally:
            if self._ticker:
                self._ticker.cancel()
                self._ticker = None
            self._installing = False
            self._stage = None
            self._old_id = None
            await self.coordinator.async_request_refresh()
            self._sync_attrs()
            self.async_write_ha_state()

    async def _wait_settled(self) -> None:
        """Wait until WUD indexed the replacement container (new id)."""
        deadline = time.monotonic() + SETTLE_TIMEOUT
        while time.monotonic() < deadline:
            try:
                containers = await self.coordinator.api.get_containers()
            except WudError:
                containers = []
            for item in containers:
                if container_key(item) == self._key and (
                    item.get("id") != self._old_id or not item.get("updateAvailable")
                ):
                    data = dict(self.coordinator.data or {})
                    data[self._key] = item
                    self.coordinator.async_set_updated_data(data)
                    return
            await asyncio.sleep(SETTLE_POLL)

    @callback
    def _on_container_event(self, event_type: str, container: dict[str, Any]) -> None:
        if not self._installing or container_key(container) != self._key:
            return
        same = container.get("id") == self._old_id
        if event_type == EVENT_CONTAINER_UPDATED and same:
            if container.get("status") not in ("running", "restarting"):
                self._set_stage(Stage.STOPPED)
        elif event_type == EVENT_CONTAINER_REMOVED and same:
            self._set_stage(Stage.REMOVED)
        elif (
            event_type in (EVENT_CONTAINER_ADDED, EVENT_CONTAINER_UPDATED) and not same
        ):
            self._set_stage(
                Stage.STARTED if container.get("status") == "running" else Stage.CREATED
            )

    @callback
    def _set_stage(self, stage: Stage) -> None:
        # Stages only move forward (events may arrive out of order)
        if self._stage is not None and _ORDER.index(stage) <= _ORDER.index(self._stage):
            return
        self._stage = stage
        self._stage_since = time.monotonic()
        self._percentage = max(self._percentage, STAGES[stage][0])
        _LOGGER.debug("%s update stage: %s (%s%%)", self._key, stage, self._percentage)
        self._write_progress()

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(PROGRESS_TICK)
            if self._stage is None:
                continue
            value = stage_progress(self._stage, time.monotonic() - self._stage_since)
            if value > self._percentage:
                self._percentage = value
                self._write_progress()

    @callback
    def _write_progress(self) -> None:
        self._attr_in_progress = self._installing
        self._attr_update_percentage = self._percentage if self._installing else None
        if self.hass is not None:
            self.async_write_ha_state()
