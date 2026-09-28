"""Config flow for the Holavonat integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ACCEPT_DISCLAIMER,
    CONF_DESTINATION,
    CONF_DESTINATION_ID,
    CONF_DESTINATION_NAME,
    CONF_DESTINATION_QUERY,
    CONF_ORIGIN,
    CONF_ORIGIN_ID,
    CONF_ORIGIN_NAME,
    CONF_ORIGIN_QUERY,
    DOMAIN,
    SUBENTRY_ROUTE,
)
from .coordinator import GtfsAuthError, GtfsDownloadError, async_download_gtfs, gtfs_path
from .gtfs import GtfsError, Stop

_LOGGER = logging.getLogger(__name__)


def _source_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
            vol.Optional(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): TextSelector(
                TextSelectorConfig(autocomplete="username")
            ),
            vol.Optional(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
            ),
        }
    )


class HolavonatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the MÁV GTFS source."""

    VERSION = 3

    async def _async_validate_source(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Download the timetable with the given settings, stored for the first setup."""
        try:
            await async_download_gtfs(
                self.hass,
                user_input[CONF_URL].strip(),
                user_input.get(CONF_USERNAME, ""),
                user_input.get(CONF_PASSWORD, ""),
                gtfs_path(self.hass),
            )
        except GtfsAuthError:
            return {"base": "invalid_auth"}
        except GtfsDownloadError:
            _LOGGER.debug("GTFS download failed", exc_info=True)
            return {"base": "cannot_connect"}
        except GtfsError:
            return {"base": "invalid_gtfs"}
        return {}

    @staticmethod
    def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
        return {
            CONF_URL: user_input[CONF_URL].strip(),
            CONF_USERNAME: user_input.get(CONF_USERNAME, ""),
            CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
        }

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the disclaimer that must be accepted first."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_ACCEPT_DISCLAIMER):
                return await self.async_step_source()
            errors[CONF_ACCEPT_DISCLAIMER] = "disclaimer_not_accepted"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_ACCEPT_DISCLAIMER, default=False): BooleanSelector()}
            ),
            errors=errors,
        )

    async def async_step_source(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the GTFS download link and its basic auth credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate_source(user_input)
            if not errors:
                return self.async_create_entry(title="MÁV menetrend", data=self._clean(user_input))
        return self.async_show_form(
            step_id="source",
            data_schema=_source_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the download link or credentials."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate_source(user_input)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=self._clean(user_input))
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_source_schema(user_input or entry.data),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials after the download was rejected."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate_source(user_input)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=self._clean(user_input))
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_source_schema(user_input or entry.data),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ROUTE: RouteSubentryFlow}


def _stop_selector(stops: list[Stop]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=s.id, label=s.name) for s in stops],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


class RouteSubentryFlow(ConfigSubentryFlow):
    """Add a station pair. Trains are listed in both directions."""

    def __init__(self) -> None:
        self._origins: list[Stop] = []
        self._destinations: list[Stop] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Search stations by name in the loaded timetable."""
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        timetable = entry.runtime_data.gtfs.timetable

        errors: dict[str, str] = {}
        if user_input is not None:
            self._origins = timetable.search_stops(user_input[CONF_ORIGIN_QUERY])
            self._destinations = timetable.search_stops(user_input[CONF_DESTINATION_QUERY])
            if not self._origins:
                errors[CONF_ORIGIN_QUERY] = "no_station"
            if not self._destinations:
                errors[CONF_DESTINATION_QUERY] = "no_station"
            if not errors:
                return await self.async_step_select()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ORIGIN_QUERY): TextSelector(),
                    vol.Required(CONF_DESTINATION_QUERY): TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the exact stations from the search results."""
        errors: dict[str, str] = {}
        if user_input is not None:
            origin = next(s for s in self._origins if s.id == user_input[CONF_ORIGIN])
            destination = next(s for s in self._destinations if s.id == user_input[CONF_DESTINATION])
            unique_id = "|".join(sorted((origin.id, destination.id)))
            if origin.id == destination.id:
                errors["base"] = "same_station"
            elif any(s.unique_id == unique_id for s in self._get_entry().subentries.values()):
                return self.async_abort(reason="already_configured")
            else:
                return self.async_create_entry(
                    title=f"{origin.name} ⇄ {destination.name}",
                    data={
                        CONF_ORIGIN_ID: origin.id,
                        CONF_ORIGIN_NAME: origin.name,
                        CONF_DESTINATION_ID: destination.id,
                        CONF_DESTINATION_NAME: destination.name,
                    },
                    unique_id=unique_id,
                )

        return self.async_show_form(
            step_id="select",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ORIGIN, default=self._origins[0].id): _stop_selector(
                        self._origins
                    ),
                    vol.Required(
                        CONF_DESTINATION, default=self._destinations[0].id
                    ): _stop_selector(self._destinations),
                }
            ),
            errors=errors,
        )
