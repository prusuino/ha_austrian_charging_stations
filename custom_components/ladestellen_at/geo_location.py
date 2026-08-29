"""Geo-location entities for charging sites.

Radius entries only: shows one marker per physical charging site (connectors
grouped by ChargingStationId / operator+address, same grouping as the
favorite picker) that currently matches the live filters. The marker label
carries the site's availability ("6/7 frei"), so multi-connector sites no
longer stack several indistinguishable per-connector markers on identical
coordinates. Custom lightweight management instead of an external
feed-manager package — on every coordinator update (new data OR a filter
change), the currently filtered site list is diffed against the entities
already created (new ones added, stale ones removed, surviving ones updated
in place so their label stays current).

Favorite entries (single connector or whole site) intentionally get no map
marker — the radius view already covers map display, and a favorite is
tracked via its sensors instead.
"""
from __future__ import annotations

import logging

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MAX_MAP_MARKERS
from .coordinator import LadestellenAtCoordinator, icon_for_status, site_status
from .localization import localized_site_status, localized_status, t

_LOGGER = logging.getLogger(__name__)
SOURCE = "ladestellen_at"
ATTRIBUTION = "Data: ladestellen.at (E-Control Austria)"

_UNIQUE_ID_PREFIX = f"{DOMAIN}_loc_"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: LadestellenAtCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_entities: dict[str, ChargingSiteEvent] = {}

    # One-time cleanup: markers used to be one per connector (unique_id
    # without the _loc_ prefix). Their registry entries would otherwise
    # linger as unavailable orphans after the switch to per-site markers.
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain == "geo_location" and not reg_entry.unique_id.startswith(_UNIQUE_ID_PREFIX):
            registry.async_remove(reg_entry.entity_id)

    @callback
    def _sync_entities() -> None:
        locations = (coordinator.data or {}).get("filtered_locations", {})

        if len(locations) > MAX_MAP_MARKERS:
            # A big radius can match thousands of sites — creating a map
            # marker for each would overwhelm Home Assistant. Keep the
            # nearest ones; the count sensor still covers the full set.
            nearest = sorted(locations.items(), key=lambda kv: kv[1].get("distance_km") or 0)
            locations = dict(nearest[:MAX_MAP_MARKERS])
            _LOGGER.info(
                "Radius matches %d charging sites — showing only the nearest %d on the map",
                len(nearest),
                MAX_MAP_MARKERS,
            )

        new_entities = []
        for location_id, location in locations.items():
            existing = known_entities.get(location_id)
            if existing is None:
                known_entities[location_id] = ChargingSiteEvent(hass, location_id, location)
                new_entities.append(known_entities[location_id])
            else:
                existing.update_location(location)
        if new_entities:
            async_add_entities(new_entities)

        for location_id in [e for e in known_entities if e not in locations]:
            entity = known_entities.pop(location_id)
            hass.async_create_task(entity.async_remove(force_remove=True))

    entry.async_on_unload(coordinator.async_add_listener(_sync_entities))
    _sync_entities()


class ChargingSiteEvent(GeolocationEvent):
    """One physical charging site (all its connectors combined). Lifecycle
    (appearing/disappearing) is managed by the sync function above; label
    and attributes are refreshed in place on every coordinator update."""

    _attr_should_poll = False
    _attr_source = SOURCE
    _attr_attribution = ATTRIBUTION
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_has_entity_name = False
    # Hidden from Home Assistant's auto-generated default dashboard map (which
    # otherwise draws every geo_location entity in the system) — still shown
    # on a Map card that references them by source, which does not go through
    # the visible-by-default list. This applies to entities registered for the
    # first time; whether an existing entity stays hidden is the user's
    # decision alone and is never rewritten from here.
    _attr_entity_registry_visible_default = False

    def __init__(self, hass: HomeAssistant, location_id: str, location: dict) -> None:
        self._hass_ref = hass
        self._location = location
        self._attr_unique_id = f"{_UNIQUE_ID_PREFIX}{location_id}"

        name = location.get("station_name") or location.get("city") or t("station_fallback_name", hass)
        max_power = self._max_power_kw(location)
        power_text = f"{max_power:g}kW " if max_power else ""
        prefix = t("station_entity_prefix", hass)
        self._attr_name = f"{prefix} {power_text}– {name}"
        self._attr_latitude = location.get("latitude")
        self._attr_longitude = location.get("longitude")
        self._attr_distance = location.get("distance_km")

    @callback
    def update_location(self, location: dict) -> None:
        """Refresh availability/attributes in place on a coordinator update,
        so the map label stays current without recreating the entity."""
        self._location = location
        self._attr_distance = location.get("distance_km")
        if self.hass:
            self.async_write_ha_state()

    @staticmethod
    def _max_power_kw(location: dict) -> float:
        return max(
            ((c.get("power_kw") or 0) for c in location.get("connectors", {}).values()),
            default=0,
        )

    def _status_label(self) -> str:
        """The map-marker label: a single connector shows its plain status,
        a multi-connector site shows availability as "free/total" — or the
        derived site status when nothing is in service ("closed" for a
        non-24h site outside opening hours, "out of service" otherwise)."""
        location = self._location
        connectors = location.get("connectors", {})
        if len(connectors) <= 1:
            only = next(iter(connectors.values()), {})
            if only.get("status") == "OutOfService" and not location.get("open_24h"):
                return localized_site_status("closed", self._hass_ref)
            return localized_status(only.get("status"), self._hass_ref)
        status = site_status(location)
        if status in ("closed", "out_of_service"):
            return localized_site_status(status, self._hass_ref)
        return t(
            "map_label_free",
            self._hass_ref,
            free=location.get("count_available", 0),
            total=location.get("count_total", len(connectors)),
        )

    @property
    def icon(self):
        if self._location.get("count_available", 0) > 0:
            return icon_for_status("Available")
        connectors = self._location.get("connectors", {})
        statuses = {c.get("status") for c in connectors.values()}
        if statuses == {"OutOfService"}:
            return icon_for_status("OutOfService")
        if "Occupied" in statuses:
            return icon_for_status("Occupied")
        return icon_for_status(None)

    @property
    def extra_state_attributes(self):
        location = self._location
        connectors = location.get("connectors", {})
        plug_types = sorted({p for c in connectors.values() for p in (c.get("plugs") or [])})
        return {
            # Localized for display (used as the map marker label via
            # label_mode: attribute). Filtering uses the raw API values
            # internally in the coordinator, unaffected by this.
            "status": self._status_label(),
            "count_available": location.get("count_available", 0),
            "count_total": location.get("count_total", len(connectors)),
            "max_power_kw": self._max_power_kw(location),
            "plug_types": plug_types,
            "operator": location.get("operator") or t("unknown_operator", self._hass_ref),
            "street": location.get("street"),
            "city": location.get("city"),
            "postal_code": location.get("postal_code"),
            "open_24h": location.get("open_24h"),
        }
