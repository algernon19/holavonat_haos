"""The Holavonat integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import CARD_URL, DOMAIN, SUBENTRY_ROUTE
from .coordinator import (
    GtfsAuthError,
    GtfsDownloadError,
    GtfsManager,
    RealtimeCoordinator,
    RouteCoordinator,
)
from .gtfs import GtfsError

PLATFORMS: list[Platform] = [Platform.SENSOR]


@dataclass
class HolavonatData:
    gtfs: GtfsManager
    realtime: RealtimeCoordinator
    routes: dict[str, RouteCoordinator] = field(default_factory=dict)


type HolavonatConfigEntry = ConfigEntry[HolavonatData]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the dashboard card and load it on every dashboard."""
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_URL, str(Path(__file__).parent / "frontend" / "holavonat-card.js"), True
            )
        ]
    )
    integration = await async_get_integration(hass, DOMAIN)
    # The version query makes browsers fetch the new card after an update.
    add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HolavonatConfigEntry) -> bool:
    """Set up the timetable source and its routes."""
    gtfs = GtfsManager(hass, entry)
    try:
        await gtfs.async_setup()
    except GtfsAuthError as err:
        raise ConfigEntryAuthFailed("MÁV GTFS download rejected the credentials") from err
    except (GtfsDownloadError, GtfsError) as err:
        raise ConfigEntryNotReady(f"MÁV GTFS timetable is not available: {err}") from err

    realtime = RealtimeCoordinator(hass, entry)
    # Realtime is optional: departures fall back to the timetable when it is down.
    await realtime.async_refresh()

    data = HolavonatData(gtfs=gtfs, realtime=realtime)
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_ROUTE:
            continue
        route = RouteCoordinator(hass, entry, subentry, gtfs, realtime)
        await route.async_refresh()
        entry.async_on_unload(realtime.async_add_listener(route.async_recalculate))
        entry.async_on_unload(gtfs.async_add_listener(route.async_recalculate))
        data.routes[subentry.subentry_id] = route
    entry.runtime_data = data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Adding or removing a route changes which stops are parsed from the timetable.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: HolavonatConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HolavonatConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
