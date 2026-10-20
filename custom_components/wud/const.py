"""Constants for the WUD (What's Up Docker) integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "wud"

CONF_GITHUB_TOKEN: Final = "github_token"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_PORT: Final = 3000
DEFAULT_SCAN_INTERVAL: Final = 5  # minutes
DEFAULT_VERIFY_SSL: Final = True

MIN_SCAN_INTERVAL: Final = timedelta(minutes=1)

# Trigger types able to actually update a container (kept in sync with
# UPDATE_TRIGGER_TYPES in wud/app/triggers/associatedTriggers.ts).
UPDATE_TRIGGER_TYPES: Final = ("docker", "dockercompose", "command", "nomad")
PREFERRED_TRIGGER_TYPES: Final = ("docker", "dockercompose")

# SSE event types emitted by GET /api/events
EVENT_CONTAINER_ADDED: Final = "wud:container-added"
EVENT_CONTAINER_UPDATED: Final = "wud:container-updated"
EVENT_CONTAINER_REMOVED: Final = "wud:container-removed"
EVENT_WATCH_STOP: Final = "wud:watch-stop"

DEFAULT_ICON: Final = "mdi:docker"
