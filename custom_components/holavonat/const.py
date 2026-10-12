"""Constants for the Holavonat integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "holavonat"

CARD_URL: Final = "/holavonat/holavonat-card.js"

REALTIME_URL: Final = "https://cdn.holavonat.is/train_data_v3.json"
REALTIME_INTERVAL: Final = timedelta(seconds=60)
ROUTE_INTERVAL: Final = timedelta(seconds=60)

# MÁV publishes the GTFS once a day, the download runs after that.
GTFS_UPDATE_HOUR: Final = 1
GTFS_UPDATE_MINUTE: Final = 30
GTFS_RETRY_DELAY: Final = timedelta(hours=1)
GTFS_FILENAME: Final = "holavonat_gtfs.zip"

TIMEZONE: Final = "Europe/Budapest"
DEPARTURE_COUNT: Final = 3
# Timetable connections considered per direction: the ones in the lookback
# window plus the upcoming ones, so delayed trains can be reordered.
CANDIDATE_COUNT: Final = 80
# Trains scheduled this far in the past are still listed when delayed.
LOOKBACK: Final = timedelta(minutes=90)
# How far back trains are searched when trains on their way are shown.
# Covers the longest domestic direct trips.
EN_ROUTE_LOOKBACK: Final = timedelta(hours=8)

SUBENTRY_ROUTE: Final = "route"

CONF_ACCEPT_DISCLAIMER: Final = "accept_disclaimer"
CONF_SHOW_EN_ROUTE: Final = "show_en_route"
CONF_ORIGIN_QUERY: Final = "origin_query"
CONF_DESTINATION_QUERY: Final = "destination_query"
CONF_ORIGIN: Final = "origin"
CONF_DESTINATION: Final = "destination"
CONF_ORIGIN_ID: Final = "origin_id"
CONF_ORIGIN_NAME: Final = "origin_name"
CONF_DESTINATION_ID: Final = "destination_id"
CONF_DESTINATION_NAME: Final = "destination_name"

ATTRIBUTION: Final = (
    "Timetable: MÁV GTFS, realtime: holavonat.is. No guarantee of accuracy or availability."
)
