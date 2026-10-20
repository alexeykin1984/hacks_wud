"""The WUD (What's Up Docker) integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import (
    CONF_API_TOKEN,
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WudApiClient
from .const import CONF_GITHUB_TOKEN, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
from .coordinator import WudConfigEntry, WudCoordinator
from .github import GithubReleases

PLATFORMS: list[Platform] = [Platform.UPDATE]


async def async_setup_entry(hass: HomeAssistant, entry: WudConfigEntry) -> bool:
    """Set up WUD from a config entry."""
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, True)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    api = WudApiClient(
        session,
        entry.data[CONF_URL],
        username=entry.data.get(CONF_USERNAME),
        password=entry.data.get(CONF_PASSWORD),
        token=entry.data.get(CONF_API_TOKEN),
        verify_ssl=verify_ssl,
    )
    github = GithubReleases(
        async_get_clientsession(hass), entry.options.get(CONF_GITHUB_TOKEN) or None
    )
    coordinator = WudCoordinator(
        hass,
        entry,
        api,
        github,
        timedelta(minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(
        hass, coordinator.async_listen_events(), "wud event stream"
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: WudConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WudConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
