"""Realtime data from the holavonat.is feed, matched to timetable connections.

Kept free of Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .const import TIMEZONE
from .gtfs import Connection, normalize

_TZ = ZoneInfo(TIMEZONE)


def _service_day_start(stoptimes: list[dict[str, Any]], now: datetime) -> datetime:
    """Return the local midnight the trip's stoptime offsets are relative to.

    The feed gives times as seconds since the start of the service day
    (values above 86400 mean after midnight), without the date. Only running
    vehicles are in the feed, so the first scheduled departure is in the past:
    pick the latest of today/yesterday for which that holds.
    """
    first = stoptimes[0].get("scheduledDeparture") or 0
    local_now = now.astimezone(_TZ)
    for offset in (0, 1):
        start = datetime.combine(local_now.date() - timedelta(days=offset), time.min, _TZ)
        if start + timedelta(seconds=first) <= local_now + timedelta(hours=1):
            return start
    return datetime.combine(local_now.date() - timedelta(days=1), time.min, _TZ)


@dataclass(slots=True, frozen=True)
class StopRealtime:
    delay_seconds: int
    platform: str | None
    current_stop: str | None


# Key: (normalized stop name, "dep" | "arr", scheduled time)
RealtimeIndex = dict[tuple[str, str, datetime], StopRealtime]


def build_realtime_index(data: dict[str, Any] | None, now: datetime) -> RealtimeIndex:
    """Index every stop event of the running trains by stop name and scheduled time."""
    index: RealtimeIndex = {}
    for vehicle in (data or {}).get("vehiclePositions") or []:
        trip = vehicle.get("trip") or {}
        stoptimes = trip.get("stoptimes") or []
        if not stoptimes:
            continue
        base = _service_day_start(stoptimes, now)
        current = ((vehicle.get("stopRelationship") or {}).get("stop") or {}).get("name")
        for st in stoptimes:
            stop = st.get("stop") or {}
            name = normalize(stop.get("name") or "")
            platform = stop.get("platformCode") or None
            if st.get("scheduledDeparture") is not None:
                index[(name, "dep", base + timedelta(seconds=st["scheduledDeparture"]))] = (
                    StopRealtime(st.get("departureDelay") or 0, platform, current)
                )
            if st.get("scheduledArrival") is not None:
                index[(name, "arr", base + timedelta(seconds=st["scheduledArrival"]))] = (
                    StopRealtime(st.get("arrivalDelay") or 0, platform, current)
                )
    return index


@dataclass(slots=True)
class Departure:
    """A timetable connection with realtime data when the train is running."""

    connection: Connection
    departure_realtime: StopRealtime | None
    arrival_realtime: StopRealtime | None

    @property
    def realtime(self) -> bool:
        return self.departure_realtime is not None

    @property
    def expected_departure(self) -> datetime:
        delay = self.departure_realtime.delay_seconds if self.departure_realtime else 0
        return self.connection.scheduled_departure + timedelta(seconds=delay)

    @property
    def expected_arrival(self) -> datetime:
        if self.arrival_realtime:
            delay = self.arrival_realtime.delay_seconds
        elif self.departure_realtime:
            delay = self.departure_realtime.delay_seconds
        else:
            delay = 0
        return self.connection.scheduled_arrival + timedelta(seconds=delay)

    def as_dict(self) -> dict[str, Any]:
        c = self.connection
        dep = self.departure_realtime
        return {
            "train": c.name,
            "headsign": c.headsign,
            "origin": c.origin,
            "destination": c.destination,
            "scheduled_departure": c.scheduled_departure.isoformat(),
            "expected_departure": self.expected_departure.isoformat(),
            "delay_min": round(dep.delay_seconds / 60) if dep else None,
            "scheduled_arrival": c.scheduled_arrival.isoformat(),
            "expected_arrival": self.expected_arrival.isoformat(),
            "platform": dep.platform if dep else None,
            "current_stop": dep.current_stop if dep else None,
            "realtime": self.realtime,
            "replacement_bus": c.replacement_bus,
        }


def upcoming_departures(
    connections: list[Connection], index: RealtimeIndex, now: datetime, count: int
) -> list[Departure]:
    """Attach realtime data and drop trains that already left, by expected time."""
    result: list[Departure] = []
    for c in connections:
        dep = Departure(
            connection=c,
            departure_realtime=index.get((normalize(c.origin), "dep", c.scheduled_departure)),
            arrival_realtime=index.get((normalize(c.destination), "arr", c.scheduled_arrival)),
        )
        if dep.expected_departure >= now:
            result.append(dep)
    result.sort(key=lambda d: d.expected_departure)
    return result[:count]
