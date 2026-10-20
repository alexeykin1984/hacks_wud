"""GitHub release notes lookup for container updates.

Mirrors the resolution logic of WUD's MQTT Home Assistant release summary
(wud/app/triggers/providers/mqtt/GithubRelease.ts).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
import re
import time
from typing import Any
from urllib.parse import quote

import aiohttp
from awesomeversion import (
    AwesomeVersion,
    AwesomeVersionCompareException,
    AwesomeVersionStrategy,
)

_LOGGER = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
SOURCE_LABEL = "org.opencontainers.image.source"
RELEASE_SUMMARY_MAX_LENGTH = 255

FOUND_TTL = 24 * 60 * 60
NOT_FOUND_TTL = 6 * 60 * 60
TRANSIENT_TTL = 5 * 60
CACHE_MAX_ENTRIES = 500

RELEASES_PER_PAGE = 100
CHANGELOG_MAX_RELEASES = 20

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
    prerelease: bool = False

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


def installed_tag(container: dict[str, Any]) -> str | None:
    """Return the installed tag of a tag update (unknown for digest updates)."""
    update_kind = container.get("updateKind") or {}
    if update_kind.get("kind") != "tag":
        return None
    local = update_kind.get("localValue")
    return local if isinstance(local, str) and local else None


def releases_page(container: dict[str, Any]) -> str | None:
    """Return the GitHub releases page of a container image."""
    if repository := resolve_github_repository(container):
        return "https://github.com/{}/{}/releases".format(*repository)
    return None


def _version(tag: str) -> AwesomeVersion | None:
    version = AwesomeVersion(tag)
    if version.strategy in (
        AwesomeVersionStrategy.UNKNOWN,
        AwesomeVersionStrategy.SPECIALCONTAINER,
    ):
        return None
    return version


def _is_prerelease(version: AwesomeVersion) -> bool:
    return bool(
        version.alpha or version.beta or version.release_candidate or version.dev
    )


def select_releases(releases: list[Release], low: str, high: str) -> list[Release]:
    """Releases newer than `low` up to `high`, newest first.

    Pre-releases are skipped unless the update itself targets a pre-release.
    """
    low_version, high_version = _version(low), _version(high)
    if low_version is None or high_version is None:
        return []
    include_pre = _is_prerelease(high_version)
    selected: list[tuple[AwesomeVersion, Release]] = []
    for release in releases:
        version = _version(release.tag)
        if version is None:
            continue
        try:
            if not low_version < version <= high_version:
                continue
        except AwesomeVersionCompareException:
            continue
        if (
            version != high_version
            and not include_pre
            and (release.prerelease or _is_prerelease(version))
        ):
            continue
        selected.append((version, release))
    try:
        selected.sort(key=lambda item: item[0], reverse=True)
    except AwesomeVersionCompareException:
        pass  # keep GitHub order (newest first)
    return [release for _, release in selected]


def format_release_notes(releases: list[Release], all_url: str | None) -> str | None:
    """Markdown release notes of one or several releases."""
    if not releases:
        return None
    if len(releases) == 1:
        release = releases[0]
        notes = release.body or release.name
        if notes and release.url:
            notes = f"{notes}\n\n[{release.tag} on GitHub]({release.url})"
        return notes
    sections = []
    for release in releases[:CHANGELOG_MAX_RELEASES]:
        title = f"[{release.tag}]({release.url})" if release.url else release.tag
        notes = (release.body or "").strip()
        if not notes and release.name and release.name != release.tag:
            notes = release.name
        sections.append(f"## {title}\n\n{notes}" if notes else f"## {title}")
    if (hidden := len(releases) - CHANGELOG_MAX_RELEASES) > 0:
        more = (
            f"[{hidden} more releases]({all_url})"
            if all_url
            else f"{hidden} more releases"
        )
        sections.append(f"… and {more} on GitHub")
    return "\n\n---\n\n".join(sections)


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
        self._lists: dict[str, tuple[float, list[Release] | None]] = {}
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
        return await self._lookup(
            self._cache, key, lambda: self._fetch(owner, repo, tag)
        )

    async def changelog(self, container: dict[str, Any]) -> list[Release]:
        """Return every release between the installed and the new version.

        Newest first; only the target release when the installed version is
        unknown (digest updates) or not comparable with the release tags.
        """
        target = self._target(container)
        if not target:
            return []
        owner, repo, key, tag = target
        latest = await self.get(container)
        selected: list[Release] = []
        if low := installed_tag(container):
            releases = await self._lookup(
                self._lists, key, lambda: self._fetch_list(owner, repo)
            )
            selected = select_releases(releases or [], low, tag)
        if latest and not any(r.tag == latest.tag for r in selected):
            selected.insert(0, latest)
        return selected

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

    async def _lookup(
        self,
        cache: dict[str, tuple[float, Any]],
        key: str,
        fetch: Callable[[], Awaitable[tuple[Any, float]]],
    ) -> Any:
        async with self._lock:
            now = time.monotonic()
            entry = cache.get(key)
            if entry and entry[0] > now:
                return entry[1]
            if now < self._paused_until:
                return None
            try:
                value, ttl = await fetch()
            except _Paused as err:
                self._paused_until = time.monotonic() + TRANSIENT_TTL
                _LOGGER.warning(
                    "Unable to get GitHub releases for %s (%s); paused for %s min",
                    key,
                    err,
                    TRANSIENT_TTL // 60,
                )
                return None
            cache.pop(key, None)
            cache[key] = (time.monotonic() + ttl, value)
            while len(cache) > CACHE_MAX_ENTRIES:
                cache.pop(next(iter(cache)))
            return value

    async def _request(self, url: str) -> tuple[int, Any]:
        """GET a GitHub API url; raise _Paused when GitHub is unusable."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "home-assistant-wud",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with self._session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                status = resp.status
                if 200 <= status < 300:
                    return status, await resp.json()
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
                if status != 404:
                    _LOGGER.debug("GitHub request %s: HTTP %s", url, status)
                return status, None
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _Paused(str(err)) from err

    async def _fetch(
        self, owner: str, repo: str, tag: str
    ) -> tuple[Release | None, float]:
        tags = [tag]
        if alternate := _alternate_tag(tag):
            tags.append(alternate)
        for candidate in tags:
            status, data = await self._request(
                f"{GITHUB_API_URL}/repos/{quote(owner)}/{quote(repo)}"
                f"/releases/tags/{quote(candidate)}"
            )
            if status == 404:
                continue
            if not isinstance(data, dict):
                return None, TRANSIENT_TTL
            release = _parse_release(data, candidate)
            ttl = FOUND_TTL if release.summary else NOT_FOUND_TTL
            return release, ttl
        _LOGGER.debug("No GitHub release found for %s/%s@%s", owner, repo, tag)
        return None, NOT_FOUND_TTL

    async def _fetch_list(
        self, owner: str, repo: str
    ) -> tuple[list[Release] | None, float]:
        status, data = await self._request(
            f"{GITHUB_API_URL}/repos/{quote(owner)}/{quote(repo)}"
            f"/releases?per_page={RELEASES_PER_PAGE}"
        )
        if status == 404:
            return [], NOT_FOUND_TTL
        if not isinstance(data, list):
            return None, TRANSIENT_TTL
        releases = [
            _parse_release(item)
            for item in data
            if isinstance(item, dict)
            and isinstance(item.get("tag_name"), str)
            and not item.get("draft")
        ]
        return releases, FOUND_TTL


def _parse_release(data: dict[str, Any], tag: str | None = None) -> Release:
    return Release(
        tag=tag or data["tag_name"],
        name=data.get("name"),
        body=data.get("body"),
        url=data.get("html_url"),
        prerelease=bool(data.get("prerelease")),
    )


class _Paused(Exception):
    """GitHub temporarily unusable."""
