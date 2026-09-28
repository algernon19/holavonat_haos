"""Reading the MÁV GTFS timetable zip and finding direct trains between two stops.

Kept free of Home Assistant imports so it can be tested on its own. Only the
stop_times rows of the configured stops are kept in memory.
"""

from __future__ import annotations

import csv
import io
import unicodedata
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .const import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)
REQUIRED_FILES = ("stops.txt", "routes.txt", "trips.txt", "stop_times.txt")
# GTFS route_type 3: bus, used by MÁV for rail replacement services.
_ROUTE_TYPE_BUS = "3"


class GtfsError(Exception):
    """Raised when the zip is not a usable GTFS feed."""


@dataclass(slots=True, frozen=True)
class Stop:
    id: str
    name: str


@dataclass(slots=True, frozen=True)
class Connection:
    """A direct train from origin to destination on a given service day."""

    name: str
    headsign: str | None
    origin: str
    destination: str
    scheduled_departure: datetime
    scheduled_arrival: datetime
    replacement_bus: bool


@dataclass(slots=True, frozen=True)
class _Candidate:
    service_id: str
    name: str
    headsign: str | None
    departure: int
    arrival: int
    replacement_bus: bool


@dataclass(slots=True)
class _Service:
    weekdays: tuple[bool, ...] = (False,) * 7
    start: date | None = None
    end: date | None = None
    added: set[date] = field(default_factory=set)
    removed: set[date] = field(default_factory=set)

    def runs_on(self, day: date) -> bool:
        if day in self.removed:
            return False
        if day in self.added:
            return True
        return (
            self.start is not None
            and self.end is not None
            and self.start <= day <= self.end
            and self.weekdays[day.weekday()]
        )


def normalize(name: str) -> str:
    """Lowercase, accent-free, single-spaced form for searching and matching."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _parse_seconds(value: str) -> int | None:
    """GTFS time HH:MM:SS, hours may exceed 23 for trips past midnight."""
    if not value:
        return None
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def _service_day_base(day: date) -> datetime:
    """GTFS times count from noon minus 12 hours, which differs from midnight on DST days."""
    return datetime.combine(day, time(12), _TZ) - timedelta(hours=12)


def _rows(zf: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    with zf.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))


class Timetable:
    """Timetable data for a fixed set of stop pairs."""

    def __init__(
        self,
        feed_version: str | None,
        feed_end: date | None,
        stops: dict[str, Stop],
        services: dict[str, _Service],
        candidates: dict[tuple[str, str], list[_Candidate]],
    ) -> None:
        self.feed_version = feed_version
        self.feed_end = feed_end
        self.stops = stops
        self._services = services
        self._candidates = candidates

    def search_stops(self, text: str) -> list[Stop]:
        query = normalize(text)
        found = [s for s in self.stops.values() if query in normalize(s.name)]
        # Exact and prefix matches first, e.g. "Szeged" before "Szeged-Rókus".
        return sorted(
            found,
            key=lambda s: (
                normalize(s.name) != query,
                not normalize(s.name).startswith(query),
                normalize(s.name),
            ),
        )

    def connections(
        self, origin_id: str, destination_id: str, after: datetime, count: int
    ) -> list[Connection]:
        """Next direct connections departing at or after a time, over the next week."""
        candidates = self._candidates.get((origin_id, destination_id), [])
        origin = self.stops[origin_id].name
        destination = self.stops[destination_id].name
        local_after = after.astimezone(_TZ)
        result: list[Connection] = []
        # Start one day earlier: trips of yesterday's service day can run past midnight.
        for offset in range(-1, 8):
            day = local_after.date() + timedelta(days=offset)
            base = _service_day_base(day)
            for c in candidates:
                departure = base + timedelta(seconds=c.departure)
                if departure < after:
                    continue
                service = self._services.get(c.service_id)
                if service is None or not service.runs_on(day):
                    continue
                result.append(
                    Connection(
                        name=c.name,
                        headsign=c.headsign,
                        origin=origin,
                        destination=destination,
                        scheduled_departure=departure,
                        scheduled_arrival=base + timedelta(seconds=c.arrival),
                        replacement_bus=c.replacement_bus,
                    )
                )
            if offset >= 0 and len(result) >= count:
                break
        result.sort(key=lambda c: c.scheduled_departure)
        return result[:count]


def load_timetable(path: Path, pairs: Iterable[tuple[str, str]]) -> Timetable:
    """Parse the GTFS zip, keeping only trips that serve the given ordered stop pairs."""
    pairs = set(pairs)
    wanted_stops = {stop for pair in pairs for stop in pair}
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            missing = [f for f in REQUIRED_FILES if f not in names]
            if missing:
                raise GtfsError(f"Missing files in GTFS zip: {', '.join(missing)}")
            if "calendar.txt" not in names and "calendar_dates.txt" not in names:
                raise GtfsError("GTFS zip has neither calendar.txt nor calendar_dates.txt")

            stops = {
                r["stop_id"]: Stop(r["stop_id"], r["stop_name"].strip())
                for r in _rows(zf, "stops.txt")
                if r.get("location_type", "0") in ("", "0")
            }

            feed_version = feed_end = None
            if "feed_info.txt" in names:
                for r in _rows(zf, "feed_info.txt"):
                    feed_version = r.get("feed_version") or None
                    if r.get("feed_end_date"):
                        feed_end = _parse_date(r["feed_end_date"])
                    break

            services: dict[str, _Service] = {}
            if "calendar.txt" in names:
                days = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
                for r in _rows(zf, "calendar.txt"):
                    services[r["service_id"]] = _Service(
                        weekdays=tuple(r[d] == "1" for d in days),
                        start=_parse_date(r["start_date"]),
                        end=_parse_date(r["end_date"]),
                    )
            if "calendar_dates.txt" in names:
                for r in _rows(zf, "calendar_dates.txt"):
                    service = services.setdefault(r["service_id"], _Service())
                    target = service.added if r["exception_type"] == "1" else service.removed
                    target.add(_parse_date(r["date"]))

            # trip_id -> {stop_id: (stop_sequence, departure, arrival)} for the wanted stops only.
            times: dict[str, dict[str, tuple[int, int | None, int | None]]] = {}
            if wanted_stops:
                for r in _rows(zf, "stop_times.txt"):
                    if r["stop_id"] in wanted_stops:
                        times.setdefault(r["trip_id"], {})[r["stop_id"]] = (
                            int(r["stop_sequence"]),
                            _parse_seconds(r["departure_time"]),
                            _parse_seconds(r["arrival_time"]),
                        )

            candidate_trips = {trip_id for trip_id, stops_ in times.items() if len(stops_) >= 2}
            routes = {r["route_id"]: r for r in _rows(zf, "routes.txt")}
            candidates: dict[tuple[str, str], list[_Candidate]] = {pair: [] for pair in pairs}
            for trip in _rows(zf, "trips.txt"):
                if trip["trip_id"] not in candidate_trips:
                    continue
                trip_times = times[trip["trip_id"]]
                route = routes.get(trip["route_id"], {})
                name = (
                    trip.get("trip_short_name")
                    or route.get("route_long_name")
                    or route.get("route_short_name")
                    or ""
                ).strip()
                for origin, destination in pairs:
                    if origin not in trip_times or destination not in trip_times:
                        continue
                    origin_seq, departure, _ = trip_times[origin]
                    destination_seq, _, arrival = trip_times[destination]
                    if origin_seq >= destination_seq or departure is None or arrival is None:
                        continue
                    candidates[(origin, destination)].append(
                        _Candidate(
                            service_id=trip["service_id"],
                            name=name,
                            headsign=(trip.get("trip_headsign") or "").strip() or None,
                            departure=departure,
                            arrival=arrival,
                            replacement_bus=route.get("route_type") == _ROUTE_TYPE_BUS,
                        )
                    )
    except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError) as err:
        raise GtfsError(f"Invalid GTFS zip: {err}") from err

    return Timetable(feed_version, feed_end, stops, services, candidates)
