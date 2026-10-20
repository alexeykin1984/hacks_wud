"""Async client for the WUD (What's Up Docker) REST API."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
import json
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
# Running an update trigger blocks until the container is replaced
# (pull + recreate), which can take a long time for big images.
TRIGGER_TIMEOUT = aiohttp.ClientTimeout(total=60 * 60)
# The SSE stream sends a keepalive comment every 20 seconds.
EVENT_CONNECTED = "connected"

EVENTS_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=15, sock_read=90)


class WudError(Exception):
    """Base WUD error."""


class WudConnectionError(WudError):
    """WUD is unreachable."""


class WudAuthError(WudError):
    """Authentication failed."""


class WudApiClient:
    """Thin wrapper around the WUD REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self.url = url.rstrip("/")
        self._headers: dict[str, str] = {"Accept": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        elif username:
            credentials = f"{username}:{password or ''}".encode()
            self._headers["Authorization"] = (
                f"Basic {base64.b64encode(credentials).decode()}"
            )
        self._ssl: bool | None = None if verify_ssl else False

    async def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: aiohttp.ClientTimeout = REQUEST_TIMEOUT,
        json_body: Any = None,
    ) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self.url}{path}",
                headers=self._headers,
                json=json_body,
                ssl=self._ssl,
                timeout=timeout,
            ) as resp:
                if resp.status in (401, 403):
                    raise WudAuthError(f"HTTP {resp.status} on {path}")
                text = await resp.text()
                if resp.status >= 400:
                    message = text
                    try:
                        message = json.loads(text).get("message", text)
                    except (ValueError, AttributeError):
                        pass
                    raise WudError(f"HTTP {resp.status} on {path}: {message}")
                if not text:
                    return None
                try:
                    return json.loads(text)
                except ValueError:
                    return text
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WudConnectionError(f"Error requesting {path}: {err}") from err

    async def get_app(self) -> dict[str, Any]:
        """Return application info (name, version)."""
        return await self._request("GET", "/api/app")

    async def get_containers(self) -> list[dict[str, Any]]:
        """Return all watched containers."""
        return await self._request("GET", "/api/containers") or []

    async def get_container(self, container_id: str) -> dict[str, Any]:
        """Return one container."""
        return await self._request("GET", f"/api/containers/{quote(container_id)}")

    async def get_container_triggers(self, container_id: str) -> list[dict[str, Any]]:
        """Return the triggers associated with a container."""
        return (
            await self._request(
                "GET", f"/api/containers/{quote(container_id)}/triggers"
            )
            or []
        )

    async def run_container_trigger(
        self, container_id: str, trigger_type: str, trigger_name: str
    ) -> None:
        """Run a trigger on a container (blocks until the trigger finished)."""
        await self._request(
            "POST",
            f"/api/containers/{quote(container_id)}/triggers/"
            f"{quote(trigger_type)}/{quote(trigger_name)}",
            timeout=TRIGGER_TIMEOUT,
        )

    async def watch_container(self, container_id: str) -> dict[str, Any]:
        """Force a registry check of one container."""
        return await self._request(
            "POST",
            f"/api/containers/{quote(container_id)}/watch",
            timeout=TRIGGER_TIMEOUT,
        )

    async def watch_containers(self) -> list[dict[str, Any]]:
        """Force a registry check of all containers."""
        return await self._request(
            "POST", "/api/containers/watch", timeout=TRIGGER_TIMEOUT
        )

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield events from the SSE stream until it closes.

        The first item is a synthetic ``{"type": "connected"}`` event.
        """
        headers = {**self._headers, "Accept": "text/event-stream"}
        try:
            async with self._session.get(
                f"{self.url}/api/events",
                headers=headers,
                ssl=self._ssl,
                timeout=EVENTS_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise WudAuthError(f"HTTP {resp.status} on /api/events")
                if resp.status >= 400:
                    raise WudError(f"HTTP {resp.status} on /api/events")
                yield {"type": EVENT_CONNECTED}
                data_lines: list[str] = []
                async for raw in resp.content:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        payload = "\n".join(data_lines)
                        data_lines = []
                        try:
                            yield json.loads(payload)
                        except ValueError:
                            _LOGGER.debug("Ignoring malformed SSE payload: %s", payload)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            # ValueError: line longer than the stream reader buffer
            raise WudConnectionError(f"Event stream error: {err}") from err
