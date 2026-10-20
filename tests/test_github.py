"""Tests for the GitHub release notes lookup."""

from __future__ import annotations

import copy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wud.github import (
    CHANGELOG_MAX_RELEASES,
    RELEASE_SUMMARY_MAX_LENGTH,
    GithubReleases,
    Release,
    format_release_notes,
    installed_tag,
    parse_github_repository,
    release_tag,
    resolve_github_repository,
    select_releases,
    to_release_summary,
)

from .const import GITHUB_RELEASE, GITHUB_RELEASE_LIST, MATTER

RELEASES = "https://api.github.com/repos/matter-js/matterjs-server/releases/tags"
RELEASE_LIST = (
    "https://api.github.com/repos/matter-js/matterjs-server/releases?per_page=100"
)


def _release(tag: str, prerelease: bool = False, body: str | None = None) -> Release:
    return Release(
        tag=tag,
        name=tag,
        body=body if body is not None else f"notes {tag}",
        url=f"https://github.com/o/r/releases/tag/{tag}",
        prerelease=prerelease,
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/esphome/esphome", ("esphome", "esphome")),
        (
            "https://github.com/grafana/grafana/releases/tag/v13.2.4",
            ("grafana", "grafana"),
        ),
        ("git+https://github.com/o/r.git", ("o", "r")),
        ("git@github.com:o/r.git", ("o", "r")),
        ("ssh://git@github.com/o/r", ("o", "r")),
        ("https://www.github.com/o/r#readme", ("o", "r")),
        ("https://gitlab.com/o/r", None),
        ("https://github.com/../r", None),
        (None, None),
        (42, None),
    ],
)
def test_parse_github_repository(url: Any, expected: tuple[str, str] | None) -> None:
    assert parse_github_repository(url) == expected


def test_resolve_from_label_first() -> None:
    container = copy.deepcopy(MATTER)
    container["labels"]["org.opencontainers.image.source"] = "https://github.com/a/b"
    container["result"]["link"] = "https://github.com/c/d/releases/tag/1"
    assert resolve_github_repository(container) == ("a", "b")


def test_resolve_from_link() -> None:
    container = copy.deepcopy(MATTER)
    container["result"]["link"] = "https://github.com/c/d/releases/tag/1"
    assert resolve_github_repository(container) == ("c", "d")
    container["result"]["link"] = None
    container["link"] = "https://github.com/e/f"
    assert resolve_github_repository(container) == ("e", "f")


def test_resolve_from_ghcr() -> None:
    assert resolve_github_repository(MATTER) == ("matter-js", "matterjs-server")


def test_resolve_none_for_docker_hub() -> None:
    container = copy.deepcopy(MATTER)
    container["image"]["registry"]["url"] = "https://registry-1.docker.io/v2"
    assert resolve_github_repository(container) is None


def test_release_tag() -> None:
    assert release_tag(MATTER) == "1.5.0"
    no_update = {**MATTER, "updateAvailable": False}
    assert release_tag(no_update) is None
    digest = {
        **MATTER,
        "updateKind": {"kind": "digest"},
        "result": {"tag": "latest", "version": "2.1.0"},
    }
    assert release_tag(digest) == "2.1.0"
    floating = {**digest, "result": {"tag": "latest", "version": "latest"}}
    assert release_tag(floating) is None


def test_to_release_summary() -> None:
    assert to_release_summary("  notes \n") == "notes"
    assert to_release_summary("   ") is None
    assert to_release_summary(None) is None
    long = to_release_summary("x" * 1000)
    assert long is not None
    assert len(long) == RELEASE_SUMMARY_MAX_LENGTH
    assert long.endswith("…")


async def test_fetch_tries_v_prefix_and_caches(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{RELEASES}/1.5.0", status=404)
    aioclient_mock.get(f"{RELEASES}/v1.5.0", json=GITHUB_RELEASE)
    github = GithubReleases(async_get_clientsession(hass), "ghtoken")

    assert github.cached(MATTER) is None
    release = await github.get(MATTER)
    assert release is not None
    assert release.tag == "v1.5.0"
    assert release.url == GITHUB_RELEASE["html_url"]
    assert release.summary == GITHUB_RELEASE["body"]
    assert aioclient_mock.call_count == 2
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == "Bearer ghtoken"

    assert github.cached(MATTER) == release
    assert await github.get(MATTER) == release
    assert aioclient_mock.call_count == 2


async def test_not_found_is_cached(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{RELEASES}/1.5.0", status=404)
    aioclient_mock.get(f"{RELEASES}/v1.5.0", status=404)
    github = GithubReleases(async_get_clientsession(hass), None)
    assert await github.get(MATTER) is None
    assert await github.get(MATTER) is None
    assert aioclient_mock.call_count == 2


async def test_rate_limit_pauses_lookups(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"{RELEASES}/1.5.0", status=403, headers={"x-ratelimit-remaining": "0"}
    )
    github = GithubReleases(async_get_clientsession(hass), None)
    assert await github.get(MATTER) is None
    other = copy.deepcopy(MATTER)
    other["result"]["tag"] = "1.6.0"
    other["updateKind"]["remoteValue"] = "1.6.0"
    assert await github.get(other) is None
    assert aioclient_mock.call_count == 1


async def test_no_target_no_request(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    github = GithubReleases(async_get_clientsession(hass), None)
    assert await github.get({**MATTER, "updateAvailable": False}) is None
    assert aioclient_mock.call_count == 0


def test_installed_tag() -> None:
    assert installed_tag(MATTER) == "1.4.0"
    assert installed_tag({**MATTER, "updateKind": {"kind": "digest"}}) is None


def test_select_releases_range_and_order() -> None:
    releases = [
        _release("v1.3.0"),
        _release("v1.5.0"),
        _release("1.4.10"),
        _release("v1.4.0"),
        _release("v1.4.2"),
        _release("v1.6.0"),
        _release("server-v1.4.5"),
    ]
    assert [r.tag for r in select_releases(releases, "1.4.0", "1.5.0")] == [
        "v1.5.0",
        "1.4.10",
        "v1.4.2",
    ]


def test_select_releases_prereleases() -> None:
    releases = [
        _release("v2.0.0-beta.2", prerelease=True),
        _release("v2.0.0-beta.1", prerelease=True),
        _release("v1.9.0-rc1"),
        _release("v1.9.0"),
        _release("v1.8.1-ls12"),  # linuxserver build suffix, not a pre-release
    ]
    # Stable target: pre-releases skipped
    assert [r.tag for r in select_releases(releases, "1.8.0", "1.9.0")] == [
        "v1.9.0",
        "v1.8.1-ls12",
    ]
    # Pre-release target: pre-releases kept
    assert [r.tag for r in select_releases(releases, "1.9.0", "2.0.0-beta.2")] == [
        "v2.0.0-beta.2",
        "v2.0.0-beta.1",
    ]


def test_select_releases_incomparable() -> None:
    releases = [_release("v1.5.0")]
    assert select_releases(releases, "latest", "1.5.0") == []
    assert select_releases(releases, "1.4.0", "nightly-5") == []


def test_format_release_notes() -> None:
    assert format_release_notes([], None) is None
    single = _release("v1.5.0")
    assert format_release_notes([single], None) == (
        f"notes v1.5.0\n\n[v1.5.0 on GitHub]({single.url})"
    )
    no_body = Release(tag="v1.4.1", name="Bugfixes", body="", url=None)
    assert format_release_notes([single, no_body], None) == (
        f"## [v1.5.0]({single.url})\n\nnotes v1.5.0\n\n---\n\n## v1.4.1\n\nBugfixes"
    )


def test_format_release_notes_truncated() -> None:
    releases = [_release(f"v1.{i}.0") for i in range(CHANGELOG_MAX_RELEASES + 3, 0, -1)]
    notes = format_release_notes(releases, "https://github.com/o/r/releases")
    assert notes is not None
    assert notes.count("## ") == CHANGELOG_MAX_RELEASES
    assert notes.endswith(
        "… and [3 more releases](https://github.com/o/r/releases) on GitHub"
    )


async def test_changelog(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{RELEASES}/1.5.0", status=404)
    aioclient_mock.get(f"{RELEASES}/v1.5.0", json=GITHUB_RELEASE)
    aioclient_mock.get(RELEASE_LIST, json=GITHUB_RELEASE_LIST)
    github = GithubReleases(async_get_clientsession(hass), None)

    releases = await github.changelog(MATTER)
    assert [r.tag for r in releases] == ["v1.5.0", "v1.4.1"]
    calls = aioclient_mock.call_count
    assert await github.changelog(MATTER) == releases
    assert aioclient_mock.call_count == calls


async def test_changelog_digest_update_only_latest(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{RELEASES}/1.5.0", status=404)
    aioclient_mock.get(f"{RELEASES}/v1.5.0", json=GITHUB_RELEASE)
    github = GithubReleases(async_get_clientsession(hass), None)
    digest = {
        **MATTER,
        "updateKind": {"kind": "digest"},
        "result": {"tag": "latest", "version": "1.5.0"},
    }
    assert [r.tag for r in await github.changelog(digest)] == ["v1.5.0"]
    assert aioclient_mock.call_count == 2  # no release list request


async def test_changelog_list_unavailable_falls_back_to_latest(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{RELEASES}/1.5.0", status=404)
    aioclient_mock.get(f"{RELEASES}/v1.5.0", json=GITHUB_RELEASE)
    aioclient_mock.get(RELEASE_LIST, status=500)
    github = GithubReleases(async_get_clientsession(hass), None)
    assert [r.tag for r in await github.changelog(MATTER)] == ["v1.5.0"]


async def test_changelog_no_target(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    github = GithubReleases(async_get_clientsession(hass), None)
    assert await github.changelog({**MATTER, "updateAvailable": False}) == []
    assert aioclient_mock.call_count == 0
