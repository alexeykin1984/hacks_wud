"""Tests for the GitHub release notes lookup."""

from __future__ import annotations

import copy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.wud.github import (
    RELEASE_SUMMARY_MAX_LENGTH,
    GithubReleases,
    parse_github_repository,
    release_tag,
    resolve_github_repository,
    to_release_summary,
)

from .const import GITHUB_RELEASE, MATTER

RELEASES = "https://api.github.com/repos/matter-js/matterjs-server/releases/tags"


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
