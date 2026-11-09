"""Config flow tests: radius overview with API key check, favorite by EvseID
(single charge point and whole site), favorite picked near a location, a
rejected key, duplicate detection, reauthentication and the reconfigure flow.
The ladestellen.at reads are faked."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ladestellen_at.config_flow import (
    CONF_FAVORITE_SCOPE,
    CONF_MODE,
    MODE_FAVORITE,
    MODE_RADIUS,
    SCOPE_SINGLE,
    SCOPE_SITE,
)
from custom_components.ladestellen_at.const import (
    CONF_API_KEY,
    CONF_ENTRY_TYPE,
    CONF_FAVORITE_NAME,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_RADIUS_KM,
    CONF_STATION_ID,
    CONF_STATION_LOCATION_ID,
    DOMAIN,
    ENTRY_TYPE_FAVORITE,
    ENTRY_TYPE_FAVORITE_LOCATION,
    ENTRY_TYPE_RADIUS,
)
from custom_components.ladestellen_at.coordinator import _parse_point

FLOW = "custom_components.ladestellen_at.config_flow."
STATION_JSON = {
    "countryId": "AT", "operatorId": "DAE", "stationId": "S77", "operatorName": "Beispiel Energie", "label": "Parkplatz Hauptplatz",
    "street": "Hauptplatz 1", "city": "Graz", "postCode": "8010", "location": {"lat": 47.07, "lon": 15.44}, "distance": 1.2,
    "openingHours": [{"fromWeekday": "MONDAY", "fromTime": "00:00", "toWeekday": "SUNDAY", "toTime": "24:00"}],
    "points": [
        {"evseId": "AT*DAE*E0001", "status": "AVAILABLE", "connectorType": [{"key": "CCCS2"}], "capacityKw": 150},
        {"evseId": "AT*DAE*E0002", "status": "OCCUPIED", "connectorType": ["STYPE2"], "capacityKw": 22},
    ],
}
STATIONS = {p["evse_id"]: p for p in (_parse_point(STATION_JSON, pt) for pt in STATION_JSON["points"])}
SITE_ID = "AT/DAE/S77"


class _AuthError(Exception):
    status = 401


@pytest.fixture
def fake_api():
    with (
        patch(FLOW + "async_fetch_stations", return_value=STATIONS),
        patch(FLOW + "async_find_station_by_evse_id", side_effect=lambda hass, key, evse_id: STATION_JSON if evse_id in STATIONS else None),
        patch(FLOW + "is_auth_error", side_effect=lambda err: isinstance(err, _AuthError)),
    ):
        yield


async def _mode(hass, mode):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_MODE: mode})
    assert result["step_id"] == mode
    return result


async def test_radius_overview_setup_stores_the_key(hass: HomeAssistant, enable_custom_integrations, fake_api) -> None:
    result = await _mode(hass, MODE_RADIUS)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: " key-1 ", CONF_LATITUDE: 47.07, CONF_LONGITUDE: 15.44, CONF_RADIUS_KM: 15})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ENTRY_TYPE: ENTRY_TYPE_RADIUS, CONF_API_KEY: "key-1", CONF_LATITUDE: 47.07, CONF_LONGITUDE: 15.44, CONF_RADIUS_KM: 15.0}
    assert result["result"].unique_id == "radius_47.07_15.44_15.0"


async def test_rejected_key_and_unreachable_service(hass: HomeAssistant, enable_custom_integrations, fake_api) -> None:
    result = await _mode(hass, MODE_RADIUS)
    with patch(FLOW + "async_fetch_stations", side_effect=_AuthError()):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "bad", CONF_LATITUDE: 47.07, CONF_LONGITUDE: 15.44, CONF_RADIUS_KM: 15})
    assert result["errors"] == {"base": "invalid_auth"}
    with patch(FLOW + "async_fetch_stations", side_effect=OSError("down")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "key", CONF_LATITUDE: 47.07, CONF_LONGITUDE: 15.44, CONF_RADIUS_KM: 15})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_favorite_by_evse_id_offers_the_whole_site(hass: HomeAssistant, enable_custom_integrations, fake_api) -> None:
    result = await _mode(hass, MODE_FAVORITE)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "key-1", CONF_STATION_ID: " AT*DAE*E0001 "})
    assert result["step_id"] == "favorite_scope"
    assert result["description_placeholders"]["count"] == "2"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_FAVORITE_SCOPE: SCOPE_SITE, CONF_FAVORITE_NAME: "Hauptplatz"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ENTRY_TYPE: ENTRY_TYPE_FAVORITE_LOCATION, CONF_API_KEY: "key-1", CONF_STATION_LOCATION_ID: SITE_ID, CONF_FAVORITE_NAME: "Hauptplatz"}
    assert result["result"].unique_id == f"favorite_location_{SITE_ID}"


async def test_favorite_single_charge_point(hass: HomeAssistant, enable_custom_integrations, fake_api) -> None:
    result = await _mode(hass, MODE_FAVORITE)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "key-1", CONF_STATION_ID: "AT*DAE*E0002"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_FAVORITE_SCOPE: SCOPE_SINGLE})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ENTRY_TYPE: ENTRY_TYPE_FAVORITE, CONF_API_KEY: "key-1", CONF_STATION_ID: "AT*DAE*E0002", CONF_FAVORITE_NAME: None}
    assert "Parkplatz Hauptplatz" in result["title"]


async def test_unknown_evse_id_is_a_field_error(hass: HomeAssistant, enable_custom_integrations, fake_api) -> None:
    result = await _mode(hass, MODE_FAVORITE)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "key-1", CONF_STATION_ID: "AT*NOPE*E0"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_STATION_ID: "station_not_found"}


async def test_favorite_picked_near_a_location(hass: HomeAssistant, enable_custom_integrations, fake_api) -> None:
    result = await _mode(hass, MODE_FAVORITE)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "key-1", CONF_STATION_ID: "", CONF_LATITUDE: 47.07, CONF_LONGITUDE: 15.44, CONF_RADIUS_KM: 5})
    assert result["step_id"] == "favorite_pick"
    assert result["description_placeholders"] == {"count": "2"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_STATION_ID: f"LOCATION:{SITE_ID}"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STATION_LOCATION_ID] == SITE_ID
    with patch(FLOW + "async_fetch_stations", return_value={}):
        result = await _mode(hass, MODE_FAVORITE)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "key-1", CONF_STATION_ID: "", CONF_LATITUDE: 47.07, CONF_LONGITUDE: 15.44, CONF_RADIUS_KM: 5})
    assert result["errors"] == {"base": "no_stations_found"}


async def test_same_favorite_twice_is_refused(hass: HomeAssistant, enable_custom_integrations, fake_api) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="favorite_AT*DAE*E0002", title="Fav", data={CONF_ENTRY_TYPE: ENTRY_TYPE_FAVORITE, CONF_API_KEY: "k", CONF_STATION_ID: "AT*DAE*E0002"}).add_to_hass(hass)
    result = await _mode(hass, MODE_FAVORITE)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "key-1", CONF_STATION_ID: "AT*DAE*E0002"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_FAVORITE_SCOPE: SCOPE_SINGLE})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_replaces_only_the_key(hass: HomeAssistant, enable_custom_integrations, fake_api) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="radius_47.07_15.44_15.0", title="Radius", data={CONF_ENTRY_TYPE: ENTRY_TYPE_RADIUS, CONF_API_KEY: "old", CONF_LATITUDE: 47.07, CONF_LONGITUDE: 15.44, CONF_RADIUS_KM: 15.0})
    entry.add_to_hass(hass)
    with (
        patch("custom_components.ladestellen_at.async_setup_entry", return_value=True),
        patch(FLOW + "async_validate_api_key", side_effect=[_AuthError(), None]),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "still-bad"})
        assert result["errors"] == {"base": "invalid_auth"}
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: " new-key "})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "new-key"
    assert entry.data[CONF_RADIUS_KM] == 15.0


async def test_reconfigure_radius_keeps_the_filter_options(hass: HomeAssistant, enable_custom_integrations, fake_api) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="radius_47.07_15.44_15.0", title="Radius",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_RADIUS, CONF_API_KEY: "key-1", CONF_LATITUDE: 47.07, CONF_LONGITUDE: 15.44, CONF_RADIUS_KM: 15.0},
        options={"min_power_kw": 50.0},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.ladestellen_at.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id})
        assert result["step_id"] == "radius"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "key-1", CONF_LATITUDE: 47.07, CONF_LONGITUDE: 15.44, CONF_RADIUS_KM: 30})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == "radius_47.07_15.44_30.0"
    assert entry.data[CONF_RADIUS_KM] == 30.0
    assert entry.options == {"min_power_kw": 50.0}
