"""Test data mirroring real WUD API payloads."""

from __future__ import annotations

import copy
from typing import Any

URL = "http://wud.local:3000"
USERNAME = "admin"
PASSWORD = "secret"

CONFIG_DATA = {
    "url": URL,
    "username": USERNAME,
    "password": PASSWORD,
    "verify_ssl": True,
}

APP = {"name": "wud", "version": "9.3.0"}

MATTER_ID = "a1bd47c99f7752bea434fb3b3a11ce795ab8bfb4b550694ba95006fcb231ffd3"
MATTER_NEW_ID = "b2ce58daa08863cfb545fc4c4b22df8a6bc9cfc5c661705cb06117fdc342fff4"

MATTER: dict[str, Any] = {
    "id": MATTER_ID,
    "name": "matterjs-server",
    "displayName": "matterjs-server",
    "displayIcon": "mdi:docker",
    "status": "running",
    "watcher": "local",
    "stack": "dockers",
    "link": None,
    "triggerInclude": "dockercompose.app,mqtt.mosquitto",
    "labels": {"com.docker.compose.project": "dockers"},
    "image": {
        "id": "sha256:a0e4feb3",
        "registry": {"name": "ghcr.public", "url": "https://ghcr.io/v2"},
        "name": "matter-js/matterjs-server",
        "tag": {"value": "1.4.0", "semver": True},
        "digest": {"watch": False, "value": None},
        "created": "2026-08-07T09:23:07.439Z",
    },
    "result": {"tag": "1.5.0", "digest": None, "created": None, "link": None},
    "updateAvailable": True,
    "updateKind": {
        "kind": "tag",
        "localValue": "1.4.0",
        "remoteValue": "1.5.0",
        "semverDiff": "minor",
    },
    "isSnoozed": False,
    "isCoolingDown": False,
}

EXPLORER: dict[str, Any] = {
    "id": "c3df69ebb19974d0c656fd5d5c33e09b7cdadfd6d772816dc17228fed453000a",
    "name": "mqtt-explorer",
    "displayName": "mqtt-explorer",
    "displayIcon": "mdi:docker",
    "status": "running",
    "watcher": "local",
    "image": {
        "registry": {"name": "hub.public", "url": "https://registry-1.docker.io/v2"},
        "name": "smeagolworms4/mqtt-explorer",
        "tag": {"value": "latest", "semver": False},
        "digest": {"watch": True, "value": "sha256:1111111111111111aaaa"},
    },
    "result": {"tag": "latest", "digest": "sha256:1111111111111111aaaa"},
    "updateAvailable": False,
    "updateKind": {"kind": "unknown"},
}

PORTAINER: dict[str, Any] = {
    "id": "d4e07afcc2aa85e1d767ae6e6d44f1ac8debeae7e883927ed28339gfe564111b",
    "name": "portainer",
    "displayName": "Portainer CE",
    "displayIcon": "si:portainer",
    "status": "running",
    "watcher": "local",
    "labels": {
        "org.opencontainers.image.source": "https://github.com/portainer/portainer"
    },
    "image": {
        "registry": {"name": "hub.public", "url": "https://registry-1.docker.io/v2"},
        "name": "portainer/portainer-ce",
        "tag": {"value": "2.45.2", "semver": True},
    },
    "result": {
        "tag": "2.45.2",
        "link": "https://github.com/portainer/portainer/releases/2.45.2",
    },
    "updateAvailable": False,
    "updateKind": {"kind": "unknown"},
    "error": {"message": "registry timeout"},
}

TRIGGER_COMPOSE = {"id": "dockercompose.app", "type": "dockercompose", "name": "app"}
TRIGGER_MQTT = {"id": "mqtt.mosquitto", "type": "mqtt", "name": "mosquitto"}

GITHUB_RELEASE = {
    "tag_name": "v1.5.0",
    "name": "v1.5.0",
    "body": "Official release, check CHANGELOG.md for details",
    "html_url": "https://github.com/matter-js/matterjs-server/releases/tag/v1.5.0",
}


def containers() -> list[dict[str, Any]]:
    """Fresh copy of the default container list."""
    return copy.deepcopy([MATTER, EXPLORER, PORTAINER])
