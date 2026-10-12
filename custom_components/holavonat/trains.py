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
    vehicle_id: str | None = None
    stop_index: int = 0
    # Where the vehicle is along its stop list: 3.0 stopped at the 4th stop,
    # 2.5 between the 3rd and the 4th. None when the feed has no position.
    position: float | None = None
    # STOPPED_AT or IN_TRANSIT_TO, relative to current_stop.
    current_status: str | None = None


def _position(vehicle: dict[str, Any], names: list[str]) -> float | None:
    relationship = vehicle.get("stopRelationship") or {}
    name = (relationship.get("stop") or {}).get("name")
    if not name or name not in names:
        return None
    index = names.index(name)
    if relationship.get("status") == "STOPPED_AT":
        return float(index)
    if relationship.get("status") == "IN_TRANSIT_TO":
        return index - 0.5
    return None


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
        names = [(st.get("stop") or {}).get("name") or "" for st in stoptimes]
        position = _position(vehicle, names)
        vehicle_id = vehicle.get("vehicleId")
        status = (vehicle.get("stopRelationship") or {}).get("status")
        for i, st in enumerate(stoptimes):
            stop = st.get("stop") or {}
            name = normalize(stop.get("name") or "")
            platform = stop.get("platformCode") or None
            if st.get("scheduledDeparture") is not None:
                index[(name, "dep", base + timedelta(seconds=st["scheduledDeparture"]))] = (
                    StopRealtime(
                        st.get("departureDelay") or 0,
                        platform,
                        current,
                        vehicle_id,
                        i,
                        position,
                        status,
                    )
                )
            if st.get("scheduledArrival") is not None:
                index[(name, "arr", base + timedelta(seconds=st["scheduledArrival"]))] = (
                    StopRealtime(
                        st.get("arrivalDelay") or 0,
                        platform,
                        current,
                        vehicle_id,
                        i,
                        position,
                        status,
                    )
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
    def arrival_delay_seconds(self) -> int | None:
        """Delay at the destination, or the current delay when that is not known yet."""
        if self.arrival_realtime:
            return self.arrival_realtime.delay_seconds
        if self.departure_realtime:
            return self.departure_realtime.delay_seconds
        return None

    @property
    def expected_arrival(self) -> datetime:
        delay = self.arrival_delay_seconds or 0
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
            "arrival_delay_min": (
                round(self.arrival_delay_seconds / 60)
                if self.arrival_delay_seconds is not None
                else None
            ),
            "platform": dep.platform if dep else None,
            "current_stop": dep.current_stop if dep else None,
            "current_status": dep.current_status if dep else None,
            "realtime": self.realtime,
            "replacement_bus": c.replacement_bus,
        }


def _segment_state(dep: Departure) -> str | None:
    """Where the vehicle is relative to the origin and destination, from its position.

    "before": has not left the origin, "between": between the two stations,
    "after": at or past the destination. None when the position is unknown.
    """
    origin, destination = dep.departure_realtime, dep.arrival_realtime
    if (
        origin is None
        or destination is None
        or origin.position is None
        or origin.vehicle_id != destination.vehicle_id
    ):
        return None
    if origin.position <= origin.stop_index:
        return "before"
    if origin.position >= destination.stop_index:
        return "after"
    return "between"


def split_departures(
    connections: list[Connection], index: RealtimeIndex, now: datetime, count: int
) -> tuple[list[Departure], list[Departure]]:
    """Attach realtime data and split by expected times.

    Returns the next `count` trains that have not left the origin yet, and the
    trains between the origin and the destination, the most recently departed
    first. With a vehicle position in the realtime feed the position decides,
    otherwise the expected times.
    """
    upcoming: list[Departure] = []
    en_route: list[Departure] = []
    for c in connections:
        dep = Departure(
            connection=c,
            departure_realtime=index.get((normalize(c.origin), "dep", c.scheduled_departure)),
            arrival_realtime=index.get((normalize(c.destination), "arr", c.scheduled_arrival)),
        )
        state = _segment_state(dep)
        if state is None:
            # No position in the feed: decide by the expected times.
            if dep.expected_departure >= now:
                state = "before"
            elif dep.expected_arrival >= now:
                state = "between"
        if state == "before":
            upcoming.append(dep)
        elif state == "between":
            en_route.append(dep)
    upcoming.sort(key=lambda d: d.expected_departure)
    en_route.sort(key=lambda d: d.expected_departure, reverse=True)
    return upcoming[:count], en_route
