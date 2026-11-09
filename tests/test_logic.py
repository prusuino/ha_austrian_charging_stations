"""Pure logic: plug families, the registry's opening-hour ranges, charge
point parsing, grouping into sites, status and the live filters."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.ladestellen_at.const import FILTER_ALL, PLUG_FAMILY_OTHER, STATUS_AVAILABLE
from custom_components.ladestellen_at.coordinator import (
    _convert_opening_hours,
    _parse_point,
    apply_filters,
    group_by_location,
    is_open_now,
    plug_family,
    site_status,
    weekly_opening_periods,
)

ZURICH = ZoneInfo("Europe/Zurich")
STATION = {
    "countryId": "AT", "operatorId": "DAE", "stationId": "S77", "operatorName": "Beispiel Energie", "label": "Parkplatz Hauptplatz",
    "street": "Hauptplatz 1", "city": "Graz", "postCode": "8010", "location": {"lat": 47.07, "lon": 15.44}, "distance": 1.234,
    "openingHours": [{"fromWeekday": "MONDAY", "fromTime": "06:00", "toWeekday": "FRIDAY", "toTime": "22:00"},
                     {"fromWeekday": "SATURDAY", "fromTime": "08:00", "toWeekday": "SATURDAY", "toTime": "18:00"}],
    "greenEnergy": True,
}


def test_plug_families_accept_both_value_shapes():
    assert plug_family({"key": "STYPE2"}) == plug_family("CTYPE2")
    assert plug_family("CCCS2") == plug_family("IEC_62196_T2_COMBO")
    assert plug_family("DOMESTIC_F") == plug_family("NEMA_5_20")
    assert plug_family("SOMETHING_NEW") == PLUG_FAMILY_OTHER
    assert plug_family(None) == PLUG_FAMILY_OTHER


def test_opening_hour_ranges_expand_per_weekday():
    open_24h, schedule = _convert_opening_hours(STATION["openingHours"])
    assert open_24h is False
    days = {entry["on"]: entry["Period"] for entry in schedule}
    assert set(days) == {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"}
    assert days["Wednesday"] == [{"begin": "06:00", "end": "22:00"}]
    assert days["Saturday"] == [{"begin": "08:00", "end": "18:00"}]
    assert is_open_now(schedule, datetime(2026, 11, 9, 12, 0, tzinfo=ZURICH)) is True  # Monday noon
    assert is_open_now(schedule, datetime(2026, 11, 8, 12, 0, tzinfo=ZURICH)) is False  # Sunday is absent
    assert weekly_opening_periods(schedule)[5] == ["08:00–18:00"]
    assert _convert_opening_hours([{"fromWeekday": "MONDAY", "fromTime": "00:00", "toWeekday": "SUNDAY", "toTime": "24:00"}]) == (
        True, [{"on": day, "Period": [{"begin": "00:00", "end": "24:00"}]} for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]]
    )
    assert _convert_opening_hours(None) == (False, [])
    # a range wrapping the week end: Friday .. Monday
    _open, wrapped = _convert_opening_hours([{"fromWeekday": "FRIDAY", "fromTime": "18:00", "toWeekday": "MONDAY", "toTime": "06:00"}])
    assert {e["on"] for e in wrapped} == {"Friday", "Saturday", "Sunday", "Monday"}


def test_parse_point_flattens_station_and_point():
    point = {"evseId": "AT*DAE*E0001", "status": "CHARGING", "connectorType": [{"key": "CCCS2"}, {"key": "STYPE2"}], "capacityKw": 150,
             "authenticationMode": ["APP"], "priceCentKwh": 49}
    parsed = _parse_point(STATION, point)
    assert parsed["evse_id"] == "AT*DAE*E0001"
    assert parsed["charging_station_id"] == "AT/DAE/S77"
    assert parsed["status"] == "Occupied" and parsed["at_status"] == "CHARGING"
    assert parsed["latitude"] == 47.07 and parsed["longitude"] == 15.44  # from the station
    assert parsed["distance_km"] == 1.23
    assert parsed["power_kw"] == 150
    assert len(parsed["plugs"]) == 2
    assert parsed["operator"] == "Beispiel Energie" and parsed["station_name"] == "Parkplatz Hauptplatz"
    assert parsed["price_cent_kwh"] == 49 and parsed["green_energy"] is True
    assert parsed["open_24h"] is False and len(parsed["opening_times"]) == 6
    assert _parse_point(STATION, {"status": "AVAILABLE"}) is None  # no EvseID
    assert _parse_point(STATION, {"evseId": "x", "status": "WEIRD"})["status"] == "Unknown"
    own_location = _parse_point(STATION, {"evseId": "y", "location": {"lat": 47.1, "lon": 15.5}})
    assert own_location["latitude"] == 47.1


def test_group_by_location_and_status():
    points = [
        {"evseId": "AT*DAE*E0001", "status": "AVAILABLE", "connectorType": ["CCCS2"], "capacityKw": 150},
        {"evseId": "AT*DAE*E0002", "status": "OCCUPIED", "connectorType": ["STYPE2"], "capacityKw": 22},
    ]
    stations = {p["evse_id"]: p for p in (_parse_point(STATION, pt) for pt in points)}
    other = _parse_point({**STATION, "stationId": "S78", "label": "Anderswo"}, {"evseId": "AT*DAE*E0009", "status": "INOPERATIVE", "connectorType": []})
    stations[other["evse_id"]] = other
    sites = group_by_location(stations)
    assert set(sites) == {"AT/DAE/S77", "AT/DAE/S78"}
    main = sites["AT/DAE/S77"]
    assert main["count_total"] == 2 and main["count_available"] == 1 and main["is_synthetic"] is False
    assert main["station_name"] == "Parkplatz Hauptplatz"
    assert site_status({**main, "open_24h": True}) == "available"
    assert site_status({**sites["AT/DAE/S78"], "open_24h": True}) == "out_of_service"
    assert site_status(sites["AT/DAE/S78"]) == "closed"  # not 24h, every connector out of service
    assert set(apply_filters(stations, 100, FILTER_ALL, FILTER_ALL, FILTER_ALL)) == {"AT*DAE*E0001"}
    assert set(apply_filters(stations, 0, FILTER_ALL, STATUS_AVAILABLE, FILTER_ALL)) == {"AT*DAE*E0001"}
    assert apply_filters(stations, 0, FILTER_ALL, FILTER_ALL, "Niemand") == {}
