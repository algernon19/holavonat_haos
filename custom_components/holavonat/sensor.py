"""Next departures in both directions of each route, and the timetable status."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util, slugify

from . import HolavonatConfigEntry
from .const import (
    ATTRIBUTION,
    CONF_DESTINATION_NAME,
    CONF_ORIGIN_NAME,
    CONF_SHOW_EN_ROUTE,
    DEPARTURE_COUNT,
    DOMAIN,
)
from .coordinator import GtfsManager, RealtimeCoordinator, RouteCoordinator
from .trains import Departure

DIRECTIONS = ("outbound", "return")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HolavonatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    show_en_route = entry.options.get(CONF_SHOW_EN_ROUTE, False)
    en_route_label = "úton" if hass.config.language.startswith("hu") else "en route"
    ent_reg = er.async_get(hass)
    async_add_entities([HolavonatTimetableSensor(entry, data.gtfs, data.realtime)])
    for subentry_id, route in data.routes.items():
        subentry = entry.subentries[subentry_id]
        device = DeviceInfo(
            identifiers={(DOMAIN, subentry_id)},
            name=subentry.title,
            manufacturer="MÁV GTFS / holavonat.is",
            entry_type=DeviceEntryType.SERVICE,
        )
        a, b = subentry.data[CONF_ORIGIN_NAME], subentry.data[CONF_DESTINATION_NAME]
        stations = {"outbound": (a, b), "return": (b, a)}
        async_add_entities(
            [
                HolavonatDepartureSensor(
                    route, device, subentry_id, direction, position, *stations[direction]
                )
                for direction in DIRECTIONS
                for position in range(DEPARTURE_COUNT)
            ],
            config_subentry_id=subentry_id,
        )
        if show_en_route:
            async_add_entities(
                [
                    HolavonatEnRouteSensor(
                        route, device, subentry_id, direction, *stations[direction], en_route_label
                    )
                    for direction in DIRECTIONS
                ],
                config_subentry_id=subentry_id,
            )
        else:
            # Remove the sensors left over from when the option was enabled.
            for direction in DIRECTIONS:
                unique_id = f"{subentry_id}_{direction}_en_route"
                if entity_id := ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id):
                    ent_reg.async_remove(entity_id)


class HolavonatDepartureSensor(CoordinatorEntity[RouteCoordinator], SensorEntity):
    """Expected departure of the n-th next direct train in one direction."""

    # The name holds the direction, e.g. "Szeged → Szatymaz 1.", without the device name.
    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:train"

    def __init__(
        self,
        coordinator: RouteCoordinator,
        device: DeviceInfo,
        subentry_id: str,
        direction: str,
        position: int,
        origin: str,
        destination: str,
    ) -> None:
        super().__init__(coordinator)
        self._direction = direction
        self._position = position
        self._attr_name = f"{origin} → {destination} {position + 1}."
        # Common prefix, so one glob can exclude all departures from the logbook.
        # Only used for new entities, existing ones keep their registry entity_id.
        self.entity_id = f"sensor.{slugify(f'{DOMAIN} {origin} {destination} {position + 1}')}"
        # Always present, so the dashboard card can group sensors without a departure.
        self._static_attributes = {
            "origin": origin,
            "destination": destination,
            "direction": direction,
            "position": position + 1,
        }
        self._attr_unique_id = f"{subentry_id}_{direction}_{position + 1}"
        self._attr_device_info = device

    @property
    def _departure(self) -> Departure | None:
        departures = (self.coordinator.data or {}).get(self._direction) or []
        return departures[self._position] if self._position < len(departures) else None

    @property
    def native_value(self) -> datetime | None:
        departure = self._departure
        return departure.expected_departure if departure else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        departure = self._departure
        return {**self._static_attributes, **(departure.as_dict() if departure else {})}


class HolavonatEnRouteSensor(CoordinatorEntity[RouteCoordinator], SensorEntity):
    """Expected arrival of the most recently departed train that is still on its way."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:train-car"

    def __init__(
        self,
        coordinator: RouteCoordinator,
        device: DeviceInfo,
        subentry_id: str,
        direction: str,
        origin: str,
        destination: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._key = f"{direction}_en_route"
        self._attr_name = f"{origin} → {destination} {label}"
        self.entity_id = f"sensor.{slugify(f'{DOMAIN} {origin} {destination} en route')}"
        self._static_attributes = {
            "origin": origin,
            "destination": destination,
            "direction": direction,
            "en_route": True,
        }
        self._attr_unique_id = f"{subentry_id}_{direction}_en_route"
        self._attr_device_info = device

    @property
    def _trains(self) -> list[Departure]:
        return (self.coordinator.data or {}).get(self._key) or []

    @property
    def native_value(self) -> datetime | None:
        trains = self._trains
        return trains[0].expected_arrival if trains else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        trains = self._trains
        return {
            **self._static_attributes,
            **(trains[0].as_dict() if trains else {}),
            # Every train on its way, the most recently departed first.
            "trains": [t.as_dict() for t in trains],
        }


class HolavonatTimetableSensor(SensorEntity):
    """When the MÁV timetable was last downloaded, and the time of the realtime feed."""

    _attr_has_entity_name = True
    _attr_translation_key = "timetable"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calendar-clock"
    _attr_should_poll = False

    def __init__(
        self, entry: HolavonatConfigEntry, gtfs: GtfsManager, realtime: RealtimeCoordinator
    ) -> None:
        self._gtfs = gtfs
        self._realtime = realtime
        self._attr_unique_id = f"{entry.entry_id}_timetable"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="MÁV GTFS",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._gtfs.async_add_listener(self._handle_update))
        self.async_on_remove(self._realtime.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        return self._gtfs.last_download

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        timetable = self._gtfs.timetable
        if timetable is None:
            return None
        feed_time = dt_util.parse_datetime((self._realtime.data or {}).get("timestamp") or "")
        return {
            "feed_version": timetable.feed_version,
            "valid_until": timetable.feed_end.isoformat() if timetable.feed_end else None,
            # Generation time of the holavonat.is feed, not of the last poll.
            "realtime_updated": feed_time.isoformat() if feed_time else None,
        }
