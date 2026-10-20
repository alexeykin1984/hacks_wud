"""GitHub release notes lookup for container updates.

Mirrors the resolution logic of WUD's MQTT Home Assistant release summary
(wud/app/triggers/providers/mqtt/GithubRelease.ts).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
import time
from typing import Any
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
SOURCE_LABEL = "org.opencontainers.image.source"
RELEASE_SUMMARY_MAX_LENGTH = 255

FOUND_TTL = 24 * 60 * 60
NOT_FOUND_TTL = 6 * 60 * 60
TRANSIENT_TTL = 5 * 60
CACHE_MAX_ENTRIES = 500

_GITHUB_URL_PATTERN = re.compile(
    r"^(?:git\+)?(?:https?://|ssh://git@|git@)?(?:www\.)?github\.com[/:]"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/?#].*)?$",
    re.IGNORECASE,
)
_GHCR_IMAGE_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")


@dataclass(frozen=True, slots=True)
class Release:
    """A GitHub release."""

    tag: str
    name: str | None
    body: str | None
    url: str | None

    @property
    def summary(self) -> str | None:
        """Release notes truncated to what HA accepts as release_summary."""
        return to_release_summary(self.body) or to_release_summary(self.name)


def parse_github_repository(url: Any) -> tuple[str, str] | None:
    """Parse a GitHub url (https, git+https, ssh...) into (owner, repo)."""
    if not isinstance(url, str):
        return None
    match = _GITHUB_URL_PATTERN.match(url.strip())
    if not match:
        return None
    owner, repo = match.groups()
    if set(owner) == {"."} or set(repo) == {"."}:
        return None
    return owner, repo


def resolve_github_repository(container: dict[str, Any]) -> tuple[str, str] | None:
    """Resolve the GitHub repository publishing the releases of an image."""
    labels = container.get("labels") or {}
    if repo := parse_github_repository(labels.get(SOURCE_LABEL)):
        return repo
    result = container.get("result") or {}
    if repo := parse_github_repository(result.get("link")) or parse_github_repository(
        container.get("link")
    ):
        return repo
    image = container.get("image") or {}
    registry_url = (image.get("registry") or {}).get("url")
    image_name = image.get("name")
    if (
        isinstance(registry_url, str)
        and "ghcr.io" in registry_url.lower()
        and isinstance(image_name, str)
        and (match := _GHCR_IMAGE_PATTERN.match(image_name))
    ):
        return match.group(1), match.group(2)
    return None


def release_tag(container: dict[str, Any]) -> str | None:
    """Return the release tag to look up for a container update."""
    if not container.get("updateAvailable"):
        return None
    result = container.get("result") or {}
    kind = (container.get("updateKind") or {}).get("kind")
    tag = result.get("tag") if kind == "tag" else result.get("version")
    if not isinstance(tag, str) or not tag or tag in ("latest", "nightly", "edge"):
        return None
    return tag


def to_release_summary(text: Any) -> str | None:
    """Trim and truncate release notes to 255 characters."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) <= RELEASE_SUMMARY_MAX_LENGTH:
        return text
    return text[: RELEASE_SUMMARY_MAX_LENGTH - 1] + "…"


def _alternate_tag(tag: str) -> str | None:
    if re.match(r"^v\d", tag, re.IGNORECASE):
        return tag[1:]
    if re.match(r"^\d", tag):
        return f"v{tag}"
    return None


class GithubReleases:
    """Cached, rate-limit friendly GitHub release fetcher."""

    def __init__(self, session: aiohttp.ClientSession, token: str | None) -> None:
        self._session = session
        self._token = token
        self._cache: dict[str, tuple[float, Release | None]] = {}
        self._paused_until = 0.0
        self._lock = asyncio.Lock()

    def cached(self, container: dict[str, Any]) -> Release | None:
        """Return the cached release of a container update (no network)."""
        target = self._target(container)
        if not target:
            return None
        entry = self._cache.get(target[2])
        if entry and entry[0] > time.monotonic():
            return entry[1]
        return None

    async def get(self, container: dict[str, Any]) -> Release | None:
        """Return the release of a container update, fetching it if needed."""
        target = self._target(container)
        if not target:
            return None
        owner, repo, key, tag = target
        async with self._lock:
            now = time.monotonic()
            entry = self._cache.get(key)
            if entry and entry[0] > now:
                return entry[1]
            if now < self._paused_until:
                return None
            try:
                release, ttl = await self._fetch(owner, repo, tag)
            except _Paused as err:
                self._paused_until = time.monotonic() + TRANSIENT_TTL
                _LOGGER.warning(
                    "Unable to get GitHub release for %s (%s); paused for %s min",
                    key,
                    err,
                    TRANSIENT_TTL // 60,
                )
                return None
            self._store(key, release, ttl)
            return release

    @staticmethod
    def _target(container: dict[str, Any]) -> tuple[str, str, str, str] | None:
        tag = release_tag(container)
        if not tag:
            return None
        repository = resolve_github_repository(container)
        if not repository:
            return None
        owner, repo = repository
        return owner, repo, f"{owner}/{repo}@".lower() + tag, tag

    def _store(self, key: str, release: Release | None, ttl: float) -> None:
        self._cache.pop(key, None)
        self._cache[key] = (time.monotonic() + ttl, release)
        while len(self._cache) > CACHE_MAX_ENTRIES:
            self._cache.pop(next(iter(self._cache)))

    async def _fetch(
        self, owner: str, repo: str, tag: str
    ) -> tuple[Release | None, float]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "home-assistant-wud",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        tags = [tag]
        if alternate := _alternate_tag(tag):
            tags.append(alternate)
        for candidate in tags:
            url = (
                f"{GITHUB_API_URL}/repos/{quote(owner)}/{quote(repo)}"
                f"/releases/tags/{quote(candidate)}"
            )
            try:
                async with self._session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    status = resp.status
                    if status == 404:
                        continue
                    if 200 <= status < 300:
                        data = await resp.json()
                        release = Release(
                            tag=candidate,
                            name=data.get("name"),
                            body=data.get("body"),
                            url=data.get("html_url"),
                        )
                        ttl = FOUND_TTL if release.summary else NOT_FOUND_TTL
                        return release, ttl
                    rate_limited = status == 429 or (
                        status == 403
                        and (
                            resp.headers.get("x-ratelimit-remaining") == "0"
                            or "retry-after" in resp.headers
                        )
                    )
                    if rate_limited:
                        raise _Paused(
                            f"rate limit reached (HTTP {status}), configure a GitHub token"
                        )
                    if status == 401:
                        raise _Paused("GitHub rejected the configured token (HTTP 401)")
                    if status >= 500:
                        raise _Paused(f"GitHub unavailable (HTTP {status})")
                    _LOGGER.debug("GitHub release lookup %s: HTTP %s", url, status)
                    return None, TRANSIENT_TTL
            except (aiohttp.ClientError, TimeoutError) as err:
                raise _Paused(str(err)) from err
        _LOGGER.debug("No GitHub release found for %s/%s@%s", owner, repo, tag)
        return None, NOT_FOUND_TTL


class _Paused(Exception):
    """GitHub temporarily unusable."""
