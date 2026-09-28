"""Next departures in both directions of each route, and the timetable status."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HolavonatConfigEntry
from .const import (
    ATTRIBUTION,
    CONF_DESTINATION_NAME,
    CONF_ORIGIN_NAME,
    DEPARTURE_COUNT,
    DOMAIN,
)
from .coordinator import GtfsManager, RouteCoordinator
from .trains import Departure

DIRECTIONS = ("outbound", "return")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HolavonatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities([HolavonatTimetableSensor(entry, data.gtfs)])
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


class HolavonatTimetableSensor(SensorEntity):
    """When the MÁV timetable was last downloaded."""

    _attr_has_entity_name = True
    _attr_translation_key = "timetable"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calendar-clock"
    _attr_should_poll = False

    def __init__(self, entry: HolavonatConfigEntry, gtfs: GtfsManager) -> None:
        self._gtfs = gtfs
        self._attr_unique_id = f"{entry.entry_id}_timetable"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="MÁV GTFS",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._gtfs.async_add_listener(self._handle_update))

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
        return {
            "feed_version": timetable.feed_version,
            "valid_until": timetable.feed_end.isoformat() if timetable.feed_end else None,
        }
