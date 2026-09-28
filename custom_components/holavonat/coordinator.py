"""Timetable download, realtime feed and per-route departure calculation."""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
import zipfile

import aiohttp

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.storage import STORAGE_DIR
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DESTINATION_ID,
    CANDIDATE_COUNT,
    CONF_ORIGIN_ID,
    DEPARTURE_COUNT,
    DOMAIN,
    GTFS_FILENAME,
    GTFS_RETRY_DELAY,
    GTFS_UPDATE_HOUR,
    GTFS_UPDATE_MINUTE,
    LOOKBACK,
    REALTIME_INTERVAL,
    REALTIME_URL,
    ROUTE_INTERVAL,
    SUBENTRY_ROUTE,
    TIMEZONE,
)
from .gtfs import REQUIRED_FILES, GtfsError, Timetable, load_timetable
from .trains import Departure, RealtimeIndex, build_realtime_index, upcoming_departures

_LOGGER = logging.getLogger(__name__)
_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=300)


class GtfsAuthError(Exception):
    """The download link rejected the credentials."""


class GtfsDownloadError(Exception):
    """The timetable could not be downloaded."""


def gtfs_path(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(STORAGE_DIR, GTFS_FILENAME))


def _validate_and_store(content: bytes, target: Path) -> None:
    tmp = target.with_suffix(".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(content)
    try:
        with zipfile.ZipFile(tmp) as zf:
            missing = [f for f in REQUIRED_FILES if f not in zf.namelist()]
    except zipfile.BadZipFile as err:
        tmp.unlink(missing_ok=True)
        raise GtfsError("The downloaded file is not a zip") from err
    if missing:
        tmp.unlink(missing_ok=True)
        raise GtfsError(f"Missing files in GTFS zip: {', '.join(missing)}")
    os.replace(tmp, target)


async def async_download_gtfs(
    hass: HomeAssistant, url: str, username: str, password: str, target: Path
) -> None:
    """Download the GTFS zip and replace the stored copy only when it is valid."""
    session = async_get_clientsession(hass)
    auth = aiohttp.BasicAuth(username, password) if username else None
    try:
        async with session.get(url, auth=auth, timeout=_DOWNLOAD_TIMEOUT) as resp:
            if resp.status in (401, 403):
                raise GtfsAuthError
            if resp.status >= 400:
                raise GtfsDownloadError(f"HTTP {resp.status}")
            content = await resp.read()
    except (aiohttp.ClientError, TimeoutError) as err:
        # The message of aiohttp errors contains the URL, which is a private link here.
        raise GtfsDownloadError(type(err).__name__) from None
    await hass.async_add_executor_job(_validate_and_store, content, target)


def _last_scheduled_update(now: datetime) -> datetime:
    local = now.astimezone(dt_util.get_time_zone(TIMEZONE))
    scheduled = datetime.combine(
        local.date(), time(GTFS_UPDATE_HOUR, GTFS_UPDATE_MINUTE), local.tzinfo
    )
    return scheduled if scheduled <= local else scheduled - timedelta(days=1)


class GtfsManager:
    """Keeps the timetable zip up to date and parsed for the configured routes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.path = gtfs_path(hass)
        self.timetable: Timetable | None = None
        self.last_download: datetime | None = None
        self._listeners: list[Callable[[], None]] = []
        self._retry_unsub: CALLBACK_TYPE | None = None

    def _pairs(self) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for subentry in self.entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_ROUTE:
                continue
            a, b = subentry.data[CONF_ORIGIN_ID], subentry.data[CONF_DESTINATION_ID]
            pairs.update({(a, b), (b, a)})
        return pairs

    async def _async_download(self) -> None:
        data = self.entry.data
        await async_download_gtfs(
            self.hass, data[CONF_URL], data[CONF_USERNAME], data[CONF_PASSWORD], self.path
        )

    async def _async_load(self) -> None:
        self.timetable = await self.hass.async_add_executor_job(
            load_timetable, self.path, self._pairs()
        )
        mtime = await self.hass.async_add_executor_job(os.path.getmtime, self.path)
        self.last_download = dt_util.utc_from_timestamp(mtime)
        for listener in list(self._listeners):
            listener()

    async def async_setup(self) -> None:
        """Load the stored timetable, downloading it first when there is none.

        Raises GtfsAuthError, GtfsDownloadError or GtfsError.
        """
        exists = await self.hass.async_add_executor_job(self.path.exists)
        if not exists:
            await self._async_download()
        try:
            await self._async_load()
        except GtfsError:
            # A damaged local copy is replaced by a fresh download.
            await self._async_download()
            await self._async_load()

        self.entry.async_on_unload(
            async_track_time_change(
                self.hass,
                self._async_scheduled_update,
                hour=GTFS_UPDATE_HOUR,
                minute=GTFS_UPDATE_MINUTE,
                second=0,
            )
        )
        self.entry.async_on_unload(self._cancel_retry)
        if self.last_download and self.last_download < _last_scheduled_update(dt_util.utcnow()):
            self.entry.async_create_background_task(
                self.hass, self.async_update(), f"{DOMAIN}_gtfs_catch_up"
            )

    @callback
    def _cancel_retry(self) -> None:
        if self._retry_unsub:
            self._retry_unsub()
            self._retry_unsub = None

    async def _async_scheduled_update(self, _now: datetime | None = None) -> None:
        self._retry_unsub = None
        await self.async_update()

    async def async_update(self) -> None:
        """Download and load a new timetable, keeping the old one on failure."""
        self._cancel_retry()
        try:
            await self._async_download()
            await self._async_load()
        except GtfsAuthError:
            _LOGGER.error("MÁV GTFS download rejected the credentials")
            self.entry.async_start_reauth(self.hass)
            return
        except (GtfsDownloadError, GtfsError) as err:
            _LOGGER.warning(
                "MÁV GTFS update failed, keeping the previous timetable and retrying in %s: %s",
                GTFS_RETRY_DELAY,
                err,
            )
            self._retry_unsub = async_call_later(
                self.hass, GTFS_RETRY_DELAY, self._async_scheduled_update
            )
            return
        _LOGGER.info("MÁV GTFS timetable updated, version %s", self.timetable.feed_version)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)


class RealtimeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches the holavonat.is feed once per interval for all routes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_realtime",
            update_interval=REALTIME_INTERVAL,
        )
        self._etag: str | None = None
        self._index: RealtimeIndex = {}
        self._index_source: dict[str, Any] | None = None

    def realtime_index(self) -> RealtimeIndex:
        """Index of the current feed, built once per feed update."""
        if self._index_source is not self.data:
            self._index = build_realtime_index(self.data, dt_util.utcnow())
            self._index_source = self.data
        return self._index

    async def _async_update_data(self) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        headers = {"If-None-Match": self._etag} if self._etag and self.data else {}
        try:
            async with session.get(
                REALTIME_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 304:
                    return self.data
                resp.raise_for_status()
                raw = await resp.read()
                etag = resp.headers.get("ETag")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error fetching holavonat.is feed: {err}") from err

        try:
            data = await self.hass.async_add_executor_job(json.loads, raw)
        except ValueError as err:
            raise UpdateFailed(f"Invalid JSON from holavonat.is: {err}") from err
        if not isinstance(data, dict) or "vehiclePositions" not in data:
            raise UpdateFailed("Unexpected feed format from holavonat.is")
        self._etag = etag
        return data


class RouteCoordinator(DataUpdateCoordinator[dict[str, list[Departure]]]):
    """Next departures in both directions of one route, recalculated every minute."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        gtfs: GtfsManager,
        realtime: RealtimeCoordinator,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_route_{subentry.subentry_id}",
            update_interval=ROUTE_INTERVAL,
        )
        self._origin = subentry.data[CONF_ORIGIN_ID]
        self._destination = subentry.data[CONF_DESTINATION_ID]
        self._gtfs = gtfs
        self._realtime = realtime

    def _calculate(self) -> dict[str, list[Departure]]:
        timetable = self._gtfs.timetable
        if timetable is None:
            raise UpdateFailed("No timetable loaded")
        if self._origin not in timetable.stops or self._destination not in timetable.stops:
            raise UpdateFailed("A configured station is missing from the current timetable")
        now = dt_util.utcnow()
        index = self._realtime.realtime_index()
        return {
            direction: upcoming_departures(
                timetable.connections(origin, destination, now - LOOKBACK, CANDIDATE_COUNT),
                index,
                now,
                DEPARTURE_COUNT,
            )
            for direction, origin, destination in (
                ("outbound", self._origin, self._destination),
                ("return", self._destination, self._origin),
            )
        }

    async def _async_update_data(self) -> dict[str, list[Departure]]:
        return self._calculate()

    @callback
    def async_recalculate(self) -> None:
        """Recalculate right away after new realtime data or a new timetable."""
        try:
            self.async_set_updated_data(self._calculate())
        except UpdateFailed as err:
            self.async_set_update_error(err)
