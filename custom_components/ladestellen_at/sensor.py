"""Sensors: available-station count for radius entries, current status for
favorite entries (single charge point or whole site)."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    CONF_ENTRY_TYPE,
    CONF_FAVORITE_NAME,
    CONF_MIN_POWER_KW,
    CONF_OPERATOR,
    CONF_PLUG_TYPE,
    CONF_PLUG_TYPE_SENSORS,
    CONF_RADIUS_KM,
    CONF_STATION_ID,
    CONF_STATION_LOCATION_ID,
    CONF_STATUS,
    DEFAULT_MIN_POWER_KW,
    DOMAIN,
    ENTRY_TYPE_FAVORITE,
    ENTRY_TYPE_FAVORITE_LOCATION,
    FILTER_ALL,
    PLUG_TYPE_SHORT_LABELS,
    PLUG_TYPE_SLUGS,
)
from .coordinator import (
    FavoriteLocationCoordinator,
    FavoriteStationCoordinator,
    LadestellenAtCoordinator,
    icon_for_status,
    is_closed_all_day_today,
    site_status,
    weekly_opening_periods,
)
from .dashboard import async_add_location_card
from .device import device_info
from .localization import localized_site_status, localized_status, t, weekday_short

_LOGGER = logging.getLogger(__name__)
ATTRIBUTION = "Data: ladestellen.at (E-Control Austria)"


def _opening_hours_display(data: dict, hass) -> str | None:
    """Localized one-line weekly opening schedule ('Mo–Fr 08:00–20:00 ·
    Sa 07:30–18:00', or '24 h geöffnet') — None when the source has no
    schedule data for this site. Consecutive weekdays with identical hours
    are collapsed into a range; days the schedule omits are closed and left
    out. Rendered by the bundled card below the address and as a row on the
    auto-generated dashboard card."""
    if data.get("open_24h"):
        return t("opening_24h", hass)
    week = weekly_opening_periods(data.get("opening_times"))
    if not week:
        return None
    groups: list[list] = []  # [start_index, end_index, periods]
    for index in range(7):
        periods = week.get(index)
        if not periods:
            continue
        if groups and groups[-1][1] == index - 1 and groups[-1][2] == periods:
            groups[-1][1] = index
        else:
            groups.append([index, index, periods])
    parts = []
    for start, end, periods in groups:
        days = weekday_short(start, hass) if start == end else f"{weekday_short(start, hass)}–{weekday_short(end, hass)}"
        parts.append(f"{days} {', '.join(periods)}")
    return " · ".join(parts)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entry_type = entry.data.get(CONF_ENTRY_TYPE)

    if entry_type == ENTRY_TYPE_FAVORITE:
        coordinator: FavoriteStationCoordinator = hass.data[DOMAIN][entry.entry_id]
        status_sensor = FavoriteStationSensor(hass, coordinator, entry)
        power_sensor = FavoriteStationPowerSensor(hass, coordinator, entry)
        price_sensor = FavoriteStationPriceSensor(hass, coordinator, entry)
        plug_sensor = FavoriteStationPlugTypeSensor(hass, coordinator, entry)
        operator_sensor = FavoriteStationOperatorSensor(hass, coordinator, entry)
        id_sensor = FavoriteStationIdSensor(hass, coordinator, entry)
        async_add_entities([status_sensor, power_sensor, price_sensor, plug_sensor, operator_sensor, id_sensor])

        site_name = _favorite_name(entry, coordinator.data or {})
        card = {
            "type": "vertical-stack",
            "cards": [
                {"type": "custom:austrian-charging-stations-card", "entity": status_sensor.entity_id, "title": site_name},
                {
                    "type": "entities",
                    "entities": [
                        {"entity": status_sensor.entity_id, "name": t("favorite_status_name", hass)},
                        {
                            "type": "attribute",
                            "entity": status_sensor.entity_id,
                            "attribute": "opening_hours_text",
                            "name": t("opening_hours_row_name", hass),
                            "icon": "mdi:clock-outline",
                        },
                        {"entity": power_sensor.entity_id, "name": t("favorite_power_name", hass)},
                        {"entity": price_sensor.entity_id, "name": t("favorite_price_name", hass)},
                        {"entity": plug_sensor.entity_id, "name": t("favorite_plug_type_name", hass)},
                        {"entity": operator_sensor.entity_id, "name": t("favorite_operator_name", hass)},
                        {"entity": id_sensor.entity_id, "name": t("favorite_station_id_name", hass)},
                    ],
                },
            ],
        }
        hass.async_create_task(_async_add_dashboard_card(hass, entry, site_name, card))
        return

    if entry_type == ENTRY_TYPE_FAVORITE_LOCATION:
        location_coordinator: FavoriteLocationCoordinator = hass.data[DOMAIN][entry.entry_id]
        site_name = _favorite_location_name(entry, location_coordinator.data or {})
        site_slug = slugify(site_name)
        summary_sensor = FavoriteLocationSensor(hass, location_coordinator, entry)
        status_sensor = FavoriteLocationStatusSensor(hass, location_coordinator, entry, site_slug)
        async_add_entities([summary_sensor, status_sensor])

        known_connectors: dict[str, list] = {}
        known_plug_sensors: dict[str, FavoriteLocationPlugAvailableSensor] = {}
        dashboard_card_added = False

        @callback
        def _sync_connector_entities() -> None:
            nonlocal dashboard_card_added
            connectors = (location_coordinator.data or {}).get("connectors", {})

            new_evse_ids = [evse_id for evse_id in connectors if evse_id not in known_connectors]
            if new_evse_ids:
                # Index by sorted EvseID so numbering stays stable across
                # restarts (assigned once per connector, never reused).
                ordered = sorted(connectors)
                new_entities = []
                for evse_id in new_evse_ids:
                    index = ordered.index(evse_id) + 1
                    entities = [
                        FavoriteLocationConnectorStatusSensor(
                            hass, location_coordinator, entry, evse_id, index, site_slug
                        ),
                        FavoriteLocationConnectorPowerSensor(
                            hass, location_coordinator, entry, evse_id, index, site_slug
                        ),
                        FavoriteLocationConnectorPriceSensor(
                            hass, location_coordinator, entry, evse_id, index, site_slug
                        ),
                        FavoriteLocationConnectorPlugTypeSensor(
                            hass, location_coordinator, entry, evse_id, index, site_slug
                        ),
                        FavoriteLocationConnectorOperatorSensor(
                            hass, location_coordinator, entry, evse_id, index, site_slug
                        ),
                        FavoriteLocationConnectorIdSensor(
                            hass, location_coordinator, entry, evse_id, index, site_slug
                        ),
                    ]
                    known_connectors[evse_id] = entities
                    new_entities.extend(entities)
                async_add_entities(new_entities)

            for evse_id in [e for e in known_connectors if e not in connectors]:
                for entity in known_connectors.pop(evse_id):
                    hass.async_create_task(entity.async_remove(force_remove=True))

            # One available-count sensor per plug type present at the site
            # (e.g. free CCS vs free CHAdeMO at a mixed site) — created and
            # removed automatically as the site's connector mix changes.
            plug_types = sorted({p for c in connectors.values() for p in (c.get("plugs") or [])})
            new_plug_types = [p for p in plug_types if p not in known_plug_sensors]
            if new_plug_types:
                plug_entities = []
                for plug_type in new_plug_types:
                    sensor = FavoriteLocationPlugAvailableSensor(
                        hass, location_coordinator, entry, plug_type, site_slug
                    )
                    known_plug_sensors[plug_type] = sensor
                    plug_entities.append(sensor)
                async_add_entities(plug_entities)
            for plug_type in [p for p in known_plug_sensors if p not in plug_types]:
                hass.async_create_task(known_plug_sensors.pop(plug_type).async_remove(force_remove=True))

            if not dashboard_card_added and known_connectors:
                dashboard_card_added = True
                card = {
                    "type": "vertical-stack",
                    "cards": [
                        {
                            "type": "custom:austrian-charging-stations-card",
                            "entity": summary_sensor.entity_id,
                            "title": site_name,
                        },
                        {
                            "type": "entities",
                            "entities": _build_location_card_entities(
                                hass, known_connectors, known_plug_sensors, summary_sensor, status_sensor
                            ),
                        },
                    ],
                }
                hass.async_create_task(_async_add_dashboard_card(hass, entry, site_name, card))

        entry.async_on_unload(location_coordinator.async_add_listener(_sync_connector_entities))
        _sync_connector_entities()
        return

    coordinator: LadestellenAtCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ChargingStationsFreeSensor(hass, coordinator, entry)])

    # Dedicated per-plug-type available-count sensors, chosen in the
    # integration's Configure dialog. Synced live: selecting a type adds its
    # sensor, deselecting removes it — no reload needed (the entry's update
    # listener in __init__ only refreshes the coordinator, it never reloads).
    known_plug_sensors: dict[str, ChargingStationsPlugTypeFreeSensor] = {}

    @callback
    def _sync_plug_type_sensors() -> None:
        selected = entry.options.get(CONF_PLUG_TYPE_SENSORS) or []

        new_entities = []
        for plug_type in selected:
            if plug_type not in known_plug_sensors:
                sensor = ChargingStationsPlugTypeFreeSensor(hass, coordinator, entry, plug_type)
                known_plug_sensors[plug_type] = sensor
                new_entities.append(sensor)
        if new_entities:
            async_add_entities(new_entities)

        registry = er.async_get(hass)
        for plug_type in [p for p in known_plug_sensors if p not in selected]:
            sensor = known_plug_sensors.pop(plug_type)
            entity_id = sensor.entity_id

            async def _remove(sensor=sensor, entity_id=entity_id) -> None:
                await sensor.async_remove(force_remove=True)
                # Drop the registry entry too, otherwise the deselected
                # sensor lingers as a dead "no longer provided" entity.
                if registry.async_get(entity_id):
                    registry.async_remove(entity_id)

            hass.async_create_task(_remove())

    async def _options_updated(hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        _sync_plug_type_sensors()

    entry.async_on_unload(entry.add_update_listener(_options_updated))
    _sync_plug_type_sensors()


async def _async_add_dashboard_card(hass: HomeAssistant, entry: ConfigEntry, title: str, card: dict) -> None:
    """Add/refresh one favorite's card on the favorites dashboard. Best-effort
    — a dashboard problem must never break the sensors themselves."""
    try:
        await async_add_location_card(hass, entry, card)
    except Exception:  # noqa: BLE001 - dashboard setup must never break the integration
        _LOGGER.exception("Automatic favorites-dashboard card setup failed for %s", title)


def _build_location_card_entities(
    hass: HomeAssistant,
    known_connectors: dict[str, list],
    known_plug_sensors: dict[str, "FavoriteLocationPlugAvailableSensor"],
    summary_sensor,
    status_sensor,
) -> list[dict]:
    entities = [
        {"entity": summary_sensor.entity_id, "name": t("favorite_location_available_name", hass)},
    ]
    # Per-plug-type available counts, directly below the overall count they
    # break down. Also shown for single-type sites, where the row nominally
    # repeats the overall count: naming the plug type makes the card easier
    # to understand at a glance (user request).
    for plug_type in sorted(known_plug_sensors):
        entities.append(
            {
                "entity": known_plug_sensors[plug_type].entity_id,
                "name": t(
                    "favorite_location_available_plug_name",
                    hass,
                    plug=PLUG_TYPE_SHORT_LABELS.get(plug_type, plug_type),
                ),
            }
        )
    entities.append({"entity": status_sensor.entity_id, "name": t("favorite_status_name", hass)})
    entities.append(
        {
            "type": "attribute",
            "entity": summary_sensor.entity_id,
            "attribute": "opening_hours_text",
            "name": t("opening_hours_row_name", hass),
            "icon": "mdi:clock-outline",
        }
    )
    for index, evse_id in enumerate(sorted(known_connectors), start=1):
        status_entity, power_entity, price_entity, plug_entity, operator_entity, id_entity = known_connectors[evse_id]
        entities.append({"type": "section", "label": t("favorite_location_connector_prefix", hass, n=index)})
        entities.append({"entity": status_entity.entity_id, "name": t("favorite_status_name", hass)})
        entities.append({"entity": power_entity.entity_id, "name": t("favorite_power_name", hass)})
        entities.append({"entity": price_entity.entity_id, "name": t("favorite_price_name", hass)})
        entities.append({"entity": plug_entity.entity_id, "name": t("favorite_plug_type_name", hass)})
        entities.append({"entity": operator_entity.entity_id, "name": t("favorite_operator_name", hass)})
        entities.append({"entity": id_entity.entity_id, "name": t("favorite_station_id_name", hass)})
    return entities


class ChargingStationsFreeSensor(CoordinatorEntity[LadestellenAtCoordinator], SensorEntity):
    """Number of available charging stations within the configured radius that
    match the current filters (minimum power / plug type / operator)."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:ev-station"

    def __init__(self, hass: HomeAssistant, coordinator: LadestellenAtCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = t("sensor_free_name", hass)
        self._attr_unique_id = f"{entry.entry_id}_free"
        self._attr_device_info = device_info(hass, entry)
        radius = entry.data.get(CONF_RADIUS_KM)
        self.entity_id = f"sensor.at_charging_stations_available_{round(radius)}km"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("count_available_filtered", 0)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        options = self._entry.options
        return {
            "radius_km": self._entry.data.get(CONF_RADIUS_KM),
            "total_in_radius": data.get("count_total", 0),
            "total_after_filter": data.get("count_filtered", 0),
            "filter_min_power_kw": options.get(CONF_MIN_POWER_KW, DEFAULT_MIN_POWER_KW),
            "filter_plug_type": options.get(CONF_PLUG_TYPE, FILTER_ALL),
            "filter_status": options.get(CONF_STATUS, FILTER_ALL),
            "filter_operator": options.get(CONF_OPERATOR, FILTER_ALL),
            "available_plug_types": data.get("plug_types", []),
            "available_operators": data.get("operators", []),
            # Available count per plug type — independent of the plug-type
            # and status filters (see coordinator), for templates without a
            # dedicated per-plug-type sensor.
            "available_by_plug_type": data.get("available_by_plug_type", {}),
        }


class ChargingStationsPlugTypeFreeSensor(CoordinatorEntity[LadestellenAtCoordinator], SensorEntity):
    """Number of available charging stations within the radius that offer one
    specific plug type. Respects the minimum-power and operator filters but
    ignores the live plug-type and status filters — the map can be filtered
    to Type 2 without the CCS sensor dropping to 0. Created per plug type
    selected in the integration's Configure dialog."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:ev-plug-ccs2"

    def __init__(
        self, hass: HomeAssistant, coordinator: LadestellenAtCoordinator, entry: ConfigEntry, plug_type: str
    ) -> None:
        super().__init__(coordinator)
        self._plug_type = plug_type
        label = PLUG_TYPE_SHORT_LABELS.get(plug_type, plug_type)
        slug = PLUG_TYPE_SLUGS.get(plug_type) or slugify(plug_type)
        self._attr_name = t("sensor_free_plug_name", hass, plug=label)
        self._attr_unique_id = f"{entry.entry_id}_free_{slug}"
        self._attr_device_info = device_info(hass, entry)
        radius = entry.data.get(CONF_RADIUS_KM)
        self.entity_id = f"sensor.at_charging_stations_available_{round(radius)}km_{slug}"

    @property
    def native_value(self):
        return ((self.coordinator.data or {}).get("available_by_plug_type") or {}).get(self._plug_type, 0)

    @property
    def extra_state_attributes(self):
        return {"plug_type": self._plug_type}


def _favorite_name(entry: ConfigEntry, station: dict) -> str:
    return (
        entry.data.get(CONF_FAVORITE_NAME)
        or station.get("station_name")
        or station.get("city")
        or entry.data[CONF_STATION_ID]
    )


class FavoriteStationSensor(CoordinatorEntity[FavoriteStationCoordinator], SensorEntity):
    """Current status of a single pinned favorite charging station,
    independent of any location or radius."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION

    def __init__(self, hass: HomeAssistant, coordinator: FavoriteStationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._hass_ref = hass
        self._entry = entry
        name = _favorite_name(entry, coordinator.data or {})
        self._attr_name = t("favorite_status_name", hass)
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_{slugify(name)}"

    @property
    def native_value(self):
        station = self.coordinator.data
        return localized_status(station.get("status") if station else None, self._hass_ref)

    @property
    def icon(self):
        return icon_for_status((self.coordinator.data or {}).get("status"))

    @property
    def extra_state_attributes(self):
        station = self.coordinator.data or {}
        return {
            "station_id": self._entry.data[CONF_STATION_ID],
            # Raw API status alongside the localized state — the bundled
            # Lovelace card keys its colors off this, independent of language.
            "status_raw": station.get("status"),
            # Same derived status a whole-site favorite has — the card greys
            # out the tile when the site is closed even though the operator
            # still reports the connector as Available.
            "site_status": site_status(
                {
                    "connectors": {"_": station},
                    "open_24h": station.get("open_24h"),
                    "opening_times": station.get("opening_times"),
                }
            ),
            "power_kw": station.get("power_kw"),
            "plug_types": station.get("plugs"),
            "operator": station.get("operator"),
            "street": station.get("street"),
            "city": station.get("city"),
            "postal_code": station.get("postal_code"),
            "open_24h": station.get("open_24h"),
            "opening_hours": _opening_hours_display(station, self._hass_ref),
            "opening_hours_text": _opening_hours_display(station, self._hass_ref)
            or t("opening_unknown", self._hass_ref),
            "closed_all_day_today": is_closed_all_day_today(station),
            "payment_options": station.get("payment_options"),
            # Austrian-registry extras: raw API status (finer-grained than
            # the canonical vocabulary, e.g. RESERVED vs CHARGING), ad-hoc
            # price, and the operator's green-energy declaration.
            "at_status": station.get("at_status"),
            "price_cent_kwh": station.get("price_cent_kwh"),
            "free_of_charge": station.get("free_of_charge"),
            "green_energy": station.get("green_energy"),
            "last_update": station.get("last_update"),
            "latitude": station.get("latitude"),
            "longitude": station.get("longitude"),
        }


class FavoriteStationPowerSensor(CoordinatorEntity[FavoriteStationCoordinator], SensorEntity):
    """Maximum charging power (kW) available at this favorite station, as its
    own graphable/tile-able sensor rather than a status-sensor attribute."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, hass: HomeAssistant, coordinator: FavoriteStationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        name = _favorite_name(entry, coordinator.data or {})
        self._attr_name = t("favorite_power_name", hass)
        self._attr_unique_id = f"{entry.entry_id}_power"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_{slugify(name)}_power_kw"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("power_kw")


class FavoriteStationPriceSensor(CoordinatorEntity[FavoriteStationCoordinator], SensorEntity):
    """Ad-hoc price (euro cents per kWh) reported by the operator for this
    favorite charge point. 0 for explicitly free chargers; unknown when the
    operator reports no price."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_native_unit_of_measurement = "ct/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:cash"

    def __init__(self, hass: HomeAssistant, coordinator: FavoriteStationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        name = _favorite_name(entry, coordinator.data or {})
        self._attr_name = t("favorite_price_name", hass)
        self._attr_unique_id = f"{entry.entry_id}_price"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_{slugify(name)}_price_ct_kwh"

    @property
    def native_value(self):
        station = self.coordinator.data or {}
        if station.get("free_of_charge"):
            return 0
        return station.get("price_cent_kwh")


class FavoriteStationPlugTypeSensor(CoordinatorEntity[FavoriteStationCoordinator], SensorEntity):
    """Plug type(s) available at this favorite station."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:power-plug-outline"

    def __init__(self, hass: HomeAssistant, coordinator: FavoriteStationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        name = _favorite_name(entry, coordinator.data or {})
        self._attr_name = t("favorite_plug_type_name", hass)
        self._attr_unique_id = f"{entry.entry_id}_plug_type"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_{slugify(name)}_plug_type"

    @property
    def native_value(self):
        plugs = (self.coordinator.data or {}).get("plugs") or []
        return ", ".join(plugs) if plugs else None


class FavoriteStationOperatorSensor(CoordinatorEntity[FavoriteStationCoordinator], SensorEntity):
    """Operator of this favorite station."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:domain"

    def __init__(self, hass: HomeAssistant, coordinator: FavoriteStationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        name = _favorite_name(entry, coordinator.data or {})
        self._attr_name = t("favorite_operator_name", hass)
        self._attr_unique_id = f"{entry.entry_id}_operator"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_{slugify(name)}_operator"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("operator")


class FavoriteStationIdSensor(CoordinatorEntity[FavoriteStationCoordinator], SensorEntity):
    """The station's EvseID — static (set at setup) but exposed as its own
    entity for easy copy/reference from a dashboard, e.g. to set up another
    favorite for the same station elsewhere."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:identifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, coordinator: FavoriteStationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        name = _favorite_name(entry, coordinator.data or {})
        self._attr_name = t("favorite_station_id_name", hass)
        self._attr_unique_id = f"{entry.entry_id}_station_id"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_{slugify(name)}_station_id"

    @property
    def native_value(self):
        return self._entry.data[CONF_STATION_ID]


def _favorite_location_name(entry: ConfigEntry, location: dict) -> str:
    return (
        entry.data.get(CONF_FAVORITE_NAME)
        or location.get("station_name")
        or location.get("city")
        or entry.data[CONF_STATION_LOCATION_ID]
    )


class FavoriteLocationSensor(CoordinatorEntity[FavoriteLocationCoordinator], SensorEntity):
    """Summary of every charge point at a single pinned favorite site —
    count available/total, with the full connector list (each with its own
    status, power, and plug type) as an attribute. The individual connectors
    also get their own sensors below, mirroring what a single favorite
    station gets, since attributes alone aren't convenient for dashboards."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:ev-station"

    def __init__(self, hass: HomeAssistant, coordinator: FavoriteLocationCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._hass_ref = hass
        self._entry = entry
        name = _favorite_location_name(entry, coordinator.data or {})
        self._attr_name = t("favorite_location_available_name", hass)
        self._attr_unique_id = f"{entry.entry_id}_available"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_location_{slugify(name)}"

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("count_available", 0)

    @property
    def extra_state_attributes(self):
        location = self.coordinator.data or {}
        if not location:
            return {}
        connectors = [
            {
                "evse_id": evse_id,
                "status": localized_status(c.get("status"), self._hass_ref),
                # Raw API value alongside the localized display text — the
                # bundled Lovelace card keys its colors off this, independent
                # of the active language.
                "status_raw": c.get("status"),
                "at_status": c.get("at_status"),
                "plug_types": c.get("plugs"),
                "power_kw": c.get("power_kw"),
                "price_cent_kwh": c.get("price_cent_kwh"),
                "renewable": c.get("green_energy"),
            }
            # Sorted by EvseID so the card's tile order/numbering matches the
            # per-connector sensors ("Charge Point n"), which are numbered by
            # sorted EvseID — the raw dict keeps API response order, which
            # made tile #1 describe a different connector than "Charge Point
            # 1" below it (user report, 2026-07-19).
            for evse_id, c in sorted(location.get("connectors", {}).items())
        ]
        status = site_status(location)
        raw_connectors = list(location.get("connectors", {}).values())
        available_by_plug_type = {
            plug_type: sum(
                1 for c in raw_connectors if c.get("status") == "Available" and plug_type in (c.get("plugs") or [])
            )
            for plug_type in sorted({p for c in raw_connectors for p in (c.get("plugs") or [])})
        }
        return {
            "count_total": location.get("count_total", 0),
            "available_by_plug_type": available_by_plug_type,
            # Derived overall site status — "closed" distinguishes a non-24h
            # site outside opening hours from a genuinely broken 24h site.
            "site_status": status,
            "site_status_text": localized_site_status(status, self._hass_ref),
            "operator": location.get("operator"),
            "street": location.get("street"),
            "city": location.get("city"),
            "postal_code": location.get("postal_code"),
            "open_24h": location.get("open_24h"),
            # opening_hours stays None without schedule data (the card omits
            # the line then); opening_hours_text falls back to a localized
            # "Unknown" for the dashboard row, which can't express a fallback.
            "opening_hours": _opening_hours_display(location, self._hass_ref),
            "opening_hours_text": _opening_hours_display(location, self._hass_ref)
            or t("opening_unknown", self._hass_ref),
            # True on full-day closures (e.g. Sundays) — the card badge says
            # "closed today" instead of just "closed" then.
            "closed_all_day_today": is_closed_all_day_today(location),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "connectors": connectors,
        }


class FavoriteLocationStatusSensor(CoordinatorEntity[FavoriteLocationCoordinator], SensorEntity):
    """Derived overall status of the whole favorite site — "closed"
    distinguishes a non-24h site outside opening hours from a genuinely
    broken 24h site (the source has no opening-hours data, see
    coordinator.site_status)."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION

    _ICONS = {
        "available": "mdi:ev-station",
        "occupied": "mdi:car-electric",
        "closed": "mdi:clock-remove-outline",
        "out_of_service": "mdi:alert-circle-outline",
    }

    def __init__(
        self, hass: HomeAssistant, coordinator: FavoriteLocationCoordinator, entry: ConfigEntry, site_slug: str
    ) -> None:
        super().__init__(coordinator)
        self._hass_ref = hass
        self._attr_name = t("favorite_status_name", hass)
        self._attr_unique_id = f"{entry.entry_id}_site_status"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_location_{site_slug}_status"

    def _status(self) -> str:
        return site_status(self.coordinator.data or {})

    @property
    def native_value(self):
        return localized_site_status(self._status(), self._hass_ref)

    @property
    def icon(self):
        return self._ICONS.get(self._status(), "mdi:help-circle-outline")

    @property
    def extra_state_attributes(self):
        # Raw key for automations — the state itself is localized.
        return {"site_status": self._status()}


class FavoriteLocationPlugAvailableSensor(CoordinatorEntity[FavoriteLocationCoordinator], SensorEntity):
    """Number of currently available charge points at a favorite site that
    offer one specific plug type — e.g. free CCS vs free CHAdeMO at a mixed
    site, matching the bundled card's visible-plug-types filter. Created
    automatically, one per plug type present at the site."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:ev-plug-ccs2"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FavoriteLocationCoordinator,
        entry: ConfigEntry,
        plug_type: str,
        site_slug: str,
    ) -> None:
        super().__init__(coordinator)
        self._plug_type = plug_type
        label = PLUG_TYPE_SHORT_LABELS.get(plug_type, plug_type)
        slug = PLUG_TYPE_SLUGS.get(plug_type) or slugify(plug_type)
        self._attr_name = t("favorite_location_available_plug_name", hass, plug=label)
        self._attr_unique_id = f"{entry.entry_id}_available_{slug}"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_location_{site_slug}_available_{slug}"

    @property
    def native_value(self):
        connectors = (self.coordinator.data or {}).get("connectors", {})
        return sum(
            1
            for c in connectors.values()
            if c.get("status") == "Available" and self._plug_type in (c.get("plugs") or [])
        )

    @property
    def extra_state_attributes(self):
        return {"plug_type": self._plug_type}


class FavoriteLocationConnectorStatusSensor(CoordinatorEntity[FavoriteLocationCoordinator], SensorEntity):
    """Current status of one specific connector at a favorite site."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FavoriteLocationCoordinator,
        entry: ConfigEntry,
        evse_id: str,
        index: int,
        site_slug: str,
    ) -> None:
        super().__init__(coordinator)
        self._hass_ref = hass
        self._evse_id = evse_id
        prefix = t("favorite_location_connector_prefix", hass, n=index)
        self._attr_name = f"{prefix} {t('favorite_status_name', hass)}"
        self._attr_unique_id = f"{entry.entry_id}_{evse_id}_status"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_location_{site_slug}_connector_{index}_status"

    @property
    def native_value(self):
        c = (self.coordinator.data or {}).get("connectors", {}).get(self._evse_id) or {}
        return localized_status(c.get("status"), self._hass_ref)

    @property
    def icon(self):
        c = (self.coordinator.data or {}).get("connectors", {}).get(self._evse_id) or {}
        return icon_for_status(c.get("status"))


class FavoriteLocationConnectorPowerSensor(CoordinatorEntity[FavoriteLocationCoordinator], SensorEntity):
    """Charging power (kW) of one specific connector at a favorite site."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FavoriteLocationCoordinator,
        entry: ConfigEntry,
        evse_id: str,
        index: int,
        site_slug: str,
    ) -> None:
        super().__init__(coordinator)
        self._evse_id = evse_id
        prefix = t("favorite_location_connector_prefix", hass, n=index)
        self._attr_name = f"{prefix} {t('favorite_power_name', hass)}"
        self._attr_unique_id = f"{entry.entry_id}_{evse_id}_power"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_location_{site_slug}_connector_{index}_power_kw"

    @property
    def native_value(self):
        c = (self.coordinator.data or {}).get("connectors", {}).get(self._evse_id) or {}
        return c.get("power_kw")


class FavoriteLocationConnectorPriceSensor(CoordinatorEntity[FavoriteLocationCoordinator], SensorEntity):
    """Ad-hoc price (euro cents per kWh) of one specific charge point at a
    favorite site. 0 for explicitly free chargers; unknown when the operator
    reports no price."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_native_unit_of_measurement = "ct/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:cash"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FavoriteLocationCoordinator,
        entry: ConfigEntry,
        evse_id: str,
        index: int,
        site_slug: str,
    ) -> None:
        super().__init__(coordinator)
        self._evse_id = evse_id
        prefix = t("favorite_location_connector_prefix", hass, n=index)
        self._attr_name = f"{prefix} {t('favorite_price_name', hass)}"
        self._attr_unique_id = f"{entry.entry_id}_{evse_id}_price"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_location_{site_slug}_connector_{index}_price_ct_kwh"

    @property
    def native_value(self):
        c = (self.coordinator.data or {}).get("connectors", {}).get(self._evse_id) or {}
        if c.get("free_of_charge"):
            return 0
        return c.get("price_cent_kwh")


class FavoriteLocationConnectorPlugTypeSensor(CoordinatorEntity[FavoriteLocationCoordinator], SensorEntity):
    """Plug type(s) of one specific connector at a favorite site."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:power-plug-outline"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FavoriteLocationCoordinator,
        entry: ConfigEntry,
        evse_id: str,
        index: int,
        site_slug: str,
    ) -> None:
        super().__init__(coordinator)
        self._evse_id = evse_id
        prefix = t("favorite_location_connector_prefix", hass, n=index)
        self._attr_name = f"{prefix} {t('favorite_plug_type_name', hass)}"
        self._attr_unique_id = f"{entry.entry_id}_{evse_id}_plug_type"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_location_{site_slug}_connector_{index}_plug_type"

    @property
    def native_value(self):
        c = (self.coordinator.data or {}).get("connectors", {}).get(self._evse_id) or {}
        plugs = c.get("plugs") or []
        return ", ".join(plugs) if plugs else None


class FavoriteLocationConnectorOperatorSensor(CoordinatorEntity[FavoriteLocationCoordinator], SensorEntity):
    """Operator of one specific connector at a favorite site."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:domain"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FavoriteLocationCoordinator,
        entry: ConfigEntry,
        evse_id: str,
        index: int,
        site_slug: str,
    ) -> None:
        super().__init__(coordinator)
        self._evse_id = evse_id
        prefix = t("favorite_location_connector_prefix", hass, n=index)
        self._attr_name = f"{prefix} {t('favorite_operator_name', hass)}"
        self._attr_unique_id = f"{entry.entry_id}_{evse_id}_operator"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_location_{site_slug}_connector_{index}_operator"

    @property
    def native_value(self):
        c = (self.coordinator.data or {}).get("connectors", {}).get(self._evse_id) or {}
        return c.get("operator")


class FavoriteLocationConnectorIdSensor(CoordinatorEntity[FavoriteLocationCoordinator], SensorEntity):
    """The connector's own EvseID (diagnostic), for reference/copy from a
    dashboard — e.g. to set up an individual favorite for just this
    connector elsewhere."""

    _attr_has_entity_name = False
    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:identifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FavoriteLocationCoordinator,
        entry: ConfigEntry,
        evse_id: str,
        index: int,
        site_slug: str,
    ) -> None:
        super().__init__(coordinator)
        self._evse_id = evse_id
        prefix = t("favorite_location_connector_prefix", hass, n=index)
        self._attr_name = f"{prefix} {t('favorite_station_id_name', hass)}"
        self._attr_unique_id = f"{entry.entry_id}_{evse_id}_station_id"
        self._attr_device_info = device_info(hass, entry)
        self.entity_id = f"sensor.at_charging_station_favorite_location_{site_slug}_connector_{index}_station_id"

    @property
    def native_value(self):
        return self._evse_id
