"""DataUpdateCoordinator for charging stations from ladestellen.at (E-Control).

Uses the official public API of the Austrian national charging point
directory. Every request carries the user's personal API key (free
registration) plus a fixed Referer header matching the domain the key was
registered for — see const.API_REFERER and the README setup instructions.

The API models a *station* as a physical site with several charge points
(EVSEs). Internally this integration flattens every charge point into its
own station dict (one dict per EVSE, carrying the site's metadata), the same
shape the rest of the code was designed around — group_by_location() then
groups them back into sites keyed by the API's country/operator/station path.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_BASE,
    API_REFERER,
    AT_STATUS_MAP,
    CONF_API_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MIN_POWER_KW,
    CONF_OPERATOR,
    CONF_PLUG_TYPE,
    CONF_RADIUS_KM,
    CONF_STATION_ID,
    CONF_STATION_LOCATION_ID,
    CONF_STATUS,
    DEFAULT_MIN_POWER_KW,
    DOMAIN,
    FETCH_TIMEOUT_SECONDS,
    FILTER_ALL,
    KNOWN_PLUG_TYPES,
    PLUG_FAMILY_OTHER,
    PLUG_KEY_FAMILIES,
    PLUG_KEY_PREFIX_FAMILIES,
    STATUS_AVAILABLE,
    STATUS_OCCUPIED,
    UPDATE_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


async def _api_get(hass: HomeAssistant, api_key: str, path: str, params: dict | None = None):
    """Perform one authenticated API request and return the parsed JSON."""
    session = async_get_clientsession(hass)
    headers = {"apiKey": api_key, "Referer": API_REFERER}
    async with session.get(
        f"{API_BASE}{path}", params=params, headers=headers, timeout=FETCH_TIMEOUT_SECONDS
    ) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def plug_family(connector_key) -> str:
    """Normalize one raw connector-type key into its canonical plug family.

    Handles both value shapes the API uses: the proximity search returns
    connector types as objects ({"key": "STYPE2", ...}), the per-point
    endpoints return plain key strings.
    """
    key = connector_key.get("key") if isinstance(connector_key, dict) else connector_key
    key = (key or "").strip()
    family = PLUG_KEY_FAMILIES.get(key)
    if family:
        return family
    for prefix, prefix_family in PLUG_KEY_PREFIX_FAMILIES:
        if key.startswith(prefix):
            return prefix_family
    return PLUG_FAMILY_OTHER


WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
API_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _convert_opening_hours(opening_hours: list | None) -> tuple[bool, list]:
    """Convert the API's openingHours ranges into the internal per-weekday
    schedule shape, plus a derived open-24h flag.

    API shape: [{"fromWeekday": "MONDAY", "fromTime": "08:00",
                 "toWeekday": "FRIDAY", "toTime": "20:00"}, ...] — a range
    entry applies its from/to times to every weekday in the range. A single
    entry spanning MONDAY 00:00 to SUNDAY 24:00 means open around the clock.

    Internal shape (consumed by is_open_now / weekly_opening_periods):
    [{"on": "Monday", "Period": [{"begin": "08:00", "end": "20:00"}]}, ...]
    """
    entries = [e for e in _as_list(opening_hours) if isinstance(e, dict)]
    if not entries:
        return False, []
    per_day: dict[int, list[dict]] = {}
    open_24h = False
    for entry in entries:
        try:
            start = WEEKDAYS.index(str(entry.get("fromWeekday", "")).upper())
            end = WEEKDAYS.index(str(entry.get("toWeekday", "")).upper())
        except ValueError:
            continue
        begin, close = str(entry.get("fromTime") or ""), str(entry.get("toTime") or "")
        if not begin or not close:
            continue
        if start == 0 and end == 6 and begin in ("00:00", "0:00") and close == "24:00":
            open_24h = True
        days = range(start, end + 1) if start <= end else [*range(start, 7), *range(0, end + 1)]
        for day in days:
            per_day.setdefault(day, []).append({"begin": begin, "end": close})
    schedule = [{"on": API_WEEKDAYS[day], "Period": periods} for day, periods in sorted(per_day.items())]
    return open_24h, schedule


def _location_id(country: str | None, operator: str | None, station_id: str | None) -> str:
    return f"{country or 'AT'}/{operator or ''}/{station_id or ''}"


def _parse_point(station: dict, point: dict) -> dict | None:
    """Flatten one charge point plus its station's metadata into the internal
    station-dict shape all downstream code consumes."""
    evse_id = point.get("evseId")
    if not evse_id:
        return None
    location = point.get("location") or {}
    lat = location.get("lat") if isinstance(location, dict) else None
    lon = location.get("lon") if isinstance(location, dict) else None
    if lat is None:
        lat = point.get("latitude")
        lon = point.get("longitude")
    station_location = station.get("location") or {}
    if lat is None:
        lat = station_location.get("lat") if isinstance(station_location, dict) else station.get("latitude")
        lon = station_location.get("lon") if isinstance(station_location, dict) else station.get("longitude")

    raw_status = point.get("status")
    connector_types = _as_list(point.get("connectorType") or point.get("connectorTypes"))
    plugs = sorted({plug_family(c) for c in connector_types}) or [PLUG_FAMILY_OTHER]
    open_24h, opening_times = _convert_opening_hours(station.get("openingHours"))

    country = station.get("countryId") or station.get("country") or "AT"
    operator_id = station.get("operatorId") or station.get("evseOperatorId")
    station_id = station.get("stationId") or station.get("evseStationId")
    distance = station.get("distance")

    return {
        "id": evse_id,
        "evse_id": evse_id,
        "charging_station_id": _location_id(country, operator_id, station_id),
        "operator_path_id": operator_id,
        "station_path_id": station_id,
        "latitude": lat,
        "longitude": lon,
        "distance_km": round(distance, 2) if isinstance(distance, (int, float)) else None,
        "status": AT_STATUS_MAP.get(raw_status or "", "Unknown"),
        "at_status": raw_status,
        "power_kw": point.get("capacityKw") or point.get("energyInKw") or 0,
        "plugs": plugs,
        "operator": station.get("operatorName") or operator_id,
        "station_name": station.get("label"),
        "street": station.get("street"),
        "city": station.get("city"),
        "postal_code": station.get("postCode"),
        "last_update": None,
        "open_24h": open_24h,
        "opening_times": opening_times,
        "payment_options": _as_list(point.get("authenticationMode") or point.get("authenticationModes")),
        "price_cent_kwh": point.get("priceCentKwh") or point.get("priceInCentPerKwh"),
        "free_of_charge": point.get("freeOfCharge"),
        "green_energy": station.get("greenEnergy"),
    }


async def async_fetch_stations(
    hass: HomeAssistant, api_key: str, lat: float, lon: float, radius_km: float
) -> dict[str, dict]:
    """Fetch the nearest stations around a location via the proximity search
    (the only search variant that includes live per-point status), flatten
    their charge points, and cut client-side at the configured radius.

    The endpoint returns the ~100 nearest stations, so very dense areas are
    capped at the 100 nearest sites regardless of the radius."""
    data = await _api_get(hass, api_key, "/search", {"latitude": lat, "longitude": lon})
    stations: dict[str, dict] = {}
    for station in _as_list(data):
        if (station.get("stationStatus") or "ACTIVE") != "ACTIVE":
            continue
        distance = station.get("distance")
        if isinstance(distance, (int, float)) and distance > radius_km:
            continue
        for point in _as_list(station.get("points")):
            try:
                parsed = _parse_point(station, point)
            except Exception:  # noqa: BLE001 - one malformed point must never kill the whole refresh
                _LOGGER.debug("Skipping malformed point at %s", station.get("stationId"), exc_info=True)
                continue
            if parsed is not None:
                stations[parsed["id"]] = parsed
    return stations


async def _fetch_station_with_points(
    hass: HomeAssistant, api_key: str, country: str, operator: str, station_id: str
) -> dict[str, dict]:
    """Fetch one station's metadata plus all of its charge points (with live
    status) and return them flattened, keyed by evse_id."""
    base = f"/countries/{country}/operators/{operator}/stations/{station_id}"
    station = await _api_get(hass, api_key, base)
    points = await _api_get(hass, api_key, f"{base}/points")
    station.setdefault("countryId", country)
    station.setdefault("operatorId", operator)
    station.setdefault("stationId", station_id)
    connectors: dict[str, dict] = {}
    for point in _as_list(points):
        parsed = _parse_point(station, point)
        if parsed is not None:
            connectors[parsed["evse_id"]] = parsed
    return connectors


async def async_find_station_by_evse_id(hass: HomeAssistant, api_key: str, evse_id: str) -> dict | None:
    """Resolve an EvseID to its station via the registry search. Returns the
    raw station JSON (with country/operator/station ids and all points, but
    WITHOUT live status) or None if nothing matches."""
    data = await _api_get(hass, api_key, "/search/stations", {"evseId": evse_id, "maxResults": 2})
    stations = (data or {}).get("stations") or []
    return stations[0] if stations else None


async def async_fetch_station_by_id(hass: HomeAssistant, api_key: str, evse_id: str) -> dict | None:
    """Fetch a single charge point's current data (incl. live status) by its
    EvseID. Used both to validate a favorite during setup and to refresh it
    afterwards. Returns None if no charge point matches."""
    station = await async_find_station_by_evse_id(hass, api_key, evse_id)
    if station is None:
        return None
    country = station.get("evseCountryId") or "AT"
    operator = station.get("evseOperatorId")
    station_id = station.get("evseStationId")
    connectors = await _fetch_station_with_points(hass, api_key, country, operator, station_id)
    return connectors.get(evse_id)


async def async_fetch_station_location(hass: HomeAssistant, api_key: str, location_id: str) -> dict[str, dict]:
    """Fetch every charge point at one station (site). location_id is the
    'country/operator/stationId' path triple stored in the config entry."""
    try:
        country, operator, station_id = location_id.split("/", 2)
    except ValueError:
        return {}
    return await _fetch_station_with_points(hass, api_key, country, operator, station_id)


async def async_resolve_site(hass: HomeAssistant, api_key: str, station: dict) -> dict | None:
    """Expand one resolved charge point to its full site (station). The
    registry already scopes stations as physical sites, so this is a single
    station fetch — kept as its own function for parity with the setup flow's
    expectations (scope step for multi-connector sites)."""
    location_id = station.get("charging_station_id")
    if not location_id:
        return None
    connectors = await async_fetch_station_location(hass, api_key, location_id)
    if not connectors:
        return None
    groups = group_by_location(connectors)
    return groups.get(location_id)


def group_by_location(stations: dict[str, dict]) -> dict[str, dict]:
    """Group flattened charge-point dicts back into physical sites.

    The Austrian registry properly scopes a station as one physical site
    (operators must register per site), so grouping by the
    country/operator/station path id is sufficient — no address-merge
    fallback needed.

    Returns one summary dict per site: connector list plus aggregated
    count/availability, keyed by the location id."""
    groups: dict[str, dict] = {}
    for evse_id, s in stations.items():
        location_id = s.get("charging_station_id") or evse_id
        groups.setdefault(location_id, {"location_id": location_id, "connectors": {}})["connectors"][evse_id] = s

    for location_id, g in groups.items():
        connectors = g["connectors"]
        first = next(iter(connectors.values()))
        g["station_name"] = first.get("station_name")
        g["street"] = first.get("street")
        g["city"] = first.get("city")
        g["postal_code"] = first.get("postal_code")
        g["operator"] = first.get("operator")
        g["latitude"] = first.get("latitude")
        g["longitude"] = first.get("longitude")
        g["distance_km"] = first.get("distance_km")
        g["open_24h"] = first.get("open_24h")
        g["opening_times"] = first.get("opening_times") or []
        g["count_total"] = len(connectors)
        g["count_available"] = sum(1 for c in connectors.values() if c.get("status") == "Available")
        g["is_synthetic"] = False
    return groups


def _parse_hhmm(value) -> int | None:
    """'HH:MM' -> minutes since midnight, None if unparseable."""
    try:
        hours, minutes = str(value).split(":")
        return int(hours) * 60 + int(minutes)
    except (ValueError, AttributeError):
        return None


def is_open_now(opening_times: list | None, now: datetime | None = None) -> bool | None:
    """Evaluate the converted opening schedule.

    Internal format: [{"on": "Saturday", "Period": [{"begin": "07:30",
    "end": "20:00"}]}, ...] — see _convert_opening_hours.

    Stations are all in Austria, so the schedule is evaluated in
    Europe/Vienna regardless of the HA instance's timezone. Returns None
    when no schedule data is present — callers must treat that as
    "unknown", not "closed". A weekday absent from a non-empty schedule
    counts as closed on that day. An end of 24:00 means "until midnight".
    """
    if not opening_times:
        return None
    try:
        now = now or datetime.now(ZoneInfo("Europe/Vienna"))
        weekday = now.strftime("%A")
        minutes = now.hour * 60 + now.minute
        for entry in opening_times:
            if not isinstance(entry, dict) or entry.get("on") != weekday:
                continue
            for period in _as_list(entry.get("Period")):
                if not isinstance(period, dict):
                    continue
                begin = _parse_hhmm(period.get("begin"))
                end = _parse_hhmm(period.get("end"))
                if begin is None or end is None:
                    continue
                if begin <= end:
                    if begin <= minutes < end:
                        return True
                # Overnight period (e.g. 22:00-06:00) wraps past midnight
                elif minutes >= begin or minutes < end:
                    return True
        return False
    except Exception:  # noqa: BLE001 - malformed schedule data must never break a refresh
        return None


def weekly_opening_periods(opening_times: list | None) -> dict[int, list[str]] | None:
    """The converted opening schedule as weekday index (0 = Monday) ->
    sorted display periods ('07:30–20:00'). Days the schedule omits are
    absent (closed all day). Returns None when there is no usable schedule
    data at all (24h sites are represented by the open_24h flag instead)."""
    if not opening_times:
        return None
    try:
        week: dict[int, list[str]] = {}
        for entry in opening_times:
            if not isinstance(entry, dict) or entry.get("on") not in API_WEEKDAYS:
                continue
            index = API_WEEKDAYS.index(entry["on"])
            for period in _as_list(entry.get("Period")):
                if isinstance(period, dict) and period.get("begin") and period.get("end"):
                    week.setdefault(index, []).append(f"{period['begin']}–{period['end']}")
        for periods in week.values():
            periods.sort()
        return week or None
    except Exception:  # noqa: BLE001 - malformed schedule data must never break a refresh
        return None


def is_closed_all_day_today(location: dict, now: datetime | None = None) -> bool:
    """True when the schedule marks today as fully closed (no opening
    periods at all) — lets UIs say "closed today" instead of just "closed"
    (which also covers being outside today's hours)."""
    if location.get("open_24h"):
        return False
    week = weekly_opening_periods(location.get("opening_times"))
    if not week:
        return False
    now = now or datetime.now(ZoneInfo("Europe/Vienna"))
    return not week.get(now.weekday())


def site_status(location: dict) -> str:
    """Derived overall status of a whole site. Returns one of:
    available / occupied / closed / out_of_service / unknown.

    Being outside the opening hours wins even over Available connectors,
    since operators keep reporting Available while the site is
    inaccessible."""
    connectors = location.get("connectors", {})
    statuses = {c.get("status") for c in connectors.values()}
    if not location.get("open_24h") and is_open_now(location.get("opening_times")) is False:
        return "closed"
    if "Available" in statuses:
        return "available"
    if "Occupied" in statuses:
        return "occupied"
    if statuses and statuses <= {"OutOfService"}:
        return "out_of_service" if location.get("open_24h") else "closed"
    return "unknown"


def icon_for_status(status: str | None) -> str:
    if status == "Available":
        return "mdi:ev-station"
    if status == "Occupied":
        return "mdi:car-electric"
    if status == "OutOfService":
        return "mdi:alert-circle-outline"
    return "mdi:help-circle-outline"


def apply_filters(
    stations: dict[str, dict],
    min_power_kw: float,
    plug_type: str,
    status: str,
    operator: str,
) -> dict[str, dict]:
    """Filter stations using canonical (language-independent) filter values."""
    result = {}
    for station_id, s in stations.items():
        if s["power_kw"] < min_power_kw:
            continue
        if plug_type != FILTER_ALL and plug_type not in s["plugs"]:
            continue
        if status == STATUS_AVAILABLE and s["status"] != "Available":
            continue
        if status == STATUS_OCCUPIED and s["status"] != "Occupied":
            continue
        if operator != FILTER_ALL and s["operator"] != operator:
            continue
        result[station_id] = s
    return result


def _entry_api_key(entry: ConfigEntry) -> str:
    return entry.data.get(CONF_API_KEY) or ""


class LadestellenAtCoordinator(DataUpdateCoordinator[dict]):
    """Fetches charging stations within the configured radius and applies the
    current (live-adjustable) filters from the config entry's options."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )
        self._entry = entry

    async def _async_update_data(self) -> dict:
        data = self._entry.data
        try:
            all_stations = await async_fetch_stations(
                self.hass,
                _entry_api_key(self._entry),
                data[CONF_LATITUDE],
                data[CONF_LONGITUDE],
                data[CONF_RADIUS_KM],
            )
        except Exception as err:
            raise UpdateFailed(f"ladestellen.at unreachable: {err}") from err

        options = self._entry.options
        filtered = apply_filters(
            all_stations,
            options.get(CONF_MIN_POWER_KW, DEFAULT_MIN_POWER_KW),
            options.get(CONF_PLUG_TYPE, FILTER_ALL),
            options.get(CONF_STATUS, FILTER_ALL),
            options.get(CONF_OPERATOR, FILTER_ALL),
        )

        plug_types = sorted({p for s in all_stations.values() for p in s["plugs"]})
        operators = sorted({s["operator"] for s in all_stations.values() if s["operator"]})

        # Available count per plug type. Respects the min-power and operator
        # filters but deliberately ignores the plug-type and status filters —
        # those only shape the map view, and a "free CCS" sensor must not
        # drop to 0 just because the map is currently filtered to Type 2.
        base = apply_filters(
            all_stations,
            options.get(CONF_MIN_POWER_KW, DEFAULT_MIN_POWER_KW),
            FILTER_ALL,
            FILTER_ALL,
            options.get(CONF_OPERATOR, FILTER_ALL),
        )
        available_by_plug_type = {p: 0 for p in KNOWN_PLUG_TYPES}
        for s in base.values():
            if s["status"] != "Available":
                continue
            for p in s["plugs"]:
                available_by_plug_type[p] = available_by_plug_type.get(p, 0) + 1

        return {
            "all_stations": all_stations,
            "filtered_stations": filtered,
            # One entry per physical site (connectors that pass the filters,
            # grouped) — the map shows these instead of per-connector markers.
            "filtered_locations": group_by_location(filtered),
            "plug_types": plug_types,
            "operators": operators,
            "available_by_plug_type": available_by_plug_type,
            "count_total": len(all_stations),
            "count_filtered": len(filtered),
            "count_available_filtered": sum(
                1 for s in filtered.values() if s["status"] == "Available"
            ),
        }


class FavoriteStationCoordinator(DataUpdateCoordinator[dict]):
    """Fetches a single pinned favorite charge point's current status by its
    EvseID — independent of any location or radius."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )
        self._entry = entry

    async def _async_update_data(self) -> dict:
        evse_id = self._entry.data[CONF_STATION_ID]
        try:
            station = await async_fetch_station_by_id(self.hass, _entry_api_key(self._entry), evse_id)
        except Exception as err:
            raise UpdateFailed(f"ladestellen.at unreachable: {err}") from err
        if station is None:
            raise UpdateFailed(f"Charge point {evse_id} no longer found in ladestellen.at data")
        return station


class FavoriteLocationCoordinator(DataUpdateCoordinator[dict]):
    """Fetches every charge point at a single pinned site (station) by its
    country/operator/station path — independent of any search radius."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )
        self._entry = entry

    async def _async_update_data(self) -> dict:
        location_id = self._entry.data[CONF_STATION_LOCATION_ID]
        try:
            connectors = await async_fetch_station_location(self.hass, _entry_api_key(self._entry), location_id)
        except Exception as err:
            raise UpdateFailed(f"ladestellen.at unreachable: {err}") from err
        if not connectors:
            raise UpdateFailed(f"Site {location_id} no longer has any charge points in ladestellen.at data")
        groups = group_by_location(connectors)
        return groups.get(location_id) or max(groups.values(), key=lambda g: len(g["connectors"]))
