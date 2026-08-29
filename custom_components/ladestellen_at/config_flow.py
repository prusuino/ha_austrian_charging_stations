"""Config flow for the ladestellen.at charging stations integration.

Two independent kinds of entry, chosen in the first step:
- Radius overview: many stations within a live-filterable area around a
  location (the ~100 nearest sites).
- Favorite: pins exactly one specific charge point or whole site,
  independent of any location or radius. Repeatable — add the integration
  again for another favorite.

Every entry carries its own copy of the user's personal API key (free
registration at admin.ladestellen.at). The key is entered through a masked
password field and is never prefilled — not from another entry, not from
storage. When the API starts rejecting a stored key, Home Assistant opens
the reauth step (async_step_reauth), which asks for a replacement, checks it
and reloads the entry with it.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_API_KEY,
    CONF_ENTRY_TYPE,
    CONF_FAVORITE_NAME,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MIN_POWER_KW,
    CONF_PLUG_TYPE,
    CONF_PLUG_TYPE_SENSORS,
    CONF_RADIUS_KM,
    CONF_STATION_ID,
    CONF_STATION_LOCATION_ID,
    DEFAULT_MIN_POWER_KW,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    MAX_RADIUS_KM,
    ENTRY_TYPE_FAVORITE,
    ENTRY_TYPE_FAVORITE_LOCATION,
    ENTRY_TYPE_RADIUS,
    FILTER_ALL,
    KNOWN_PLUG_TYPES,
)
from .coordinator import (
    _parse_point,
    apply_filters,
    async_fetch_stations,
    async_find_station_by_evse_id,
    async_validate_api_key,
    group_by_location,
    is_auth_error,
)
from .localization import location_display_label, station_display_label, t

CONF_MODE = "mode"
MODE_RADIUS = "radius"
MODE_FAVORITE = "favorite"

CONF_FAVORITE_SCOPE = "favorite_scope"
SCOPE_SINGLE = "single"
SCOPE_SITE = "site"

_LOCATION_PREFIX = "LOCATION:"  # sentinel prefix for a whole-site pick — never collides with a real EvseID

_RADIUS_SELECTOR = NumberSelector(
    NumberSelectorConfig(min=1, max=MAX_RADIUS_KM, step=0.5, unit_of_measurement="km", mode=NumberSelectorMode.BOX)
)

# Masked input for every API-key field (radius, favorite, reauth). The key is
# a credential: a plain str field would render it in cleartext.
_API_KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class LadestellenAtConfigFlow(ConfigFlow, domain=DOMAIN):
    """Setup wizard: pick radius overview or favorite mode, then configure it."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LadestellenAtOptionsFlow()

    def __init__(self) -> None:
        self._favorite_candidates: dict[str, dict] = {}
        self._api_key: str = ""
        # Carried between favorite -> favorite_scope when an entered EvseID
        # turns out to sit at a multi-connector site.
        self._pending_station: dict | None = None
        self._pending_site: dict | None = None
        self._pending_name: str | None = None
        # Carried between favorite -> favorite_confirm (ID path).
        self._confirm_station: dict | None = None
        self._confirm_location_id: str | None = None

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Started by Home Assistant when a refresh reports the stored key as
        rejected (coordinator._refresh_error raises ConfigEntryAuthFailed)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Ask for a replacement key, check it with the same request the
        entry makes on every refresh, then store it and reload the entry.
        Only the key changes; location, radius, favorite and options stay."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            try:
                await async_validate_api_key(self.hass, api_key, entry.data)
            except Exception as err:  # noqa: BLE001 - map to form errors
                errors["base"] = "invalid_auth" if is_auth_error(err) else "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_API_KEY: api_key})

        schema = vol.Schema({vol.Required(CONF_API_KEY): _API_KEY_SELECTOR})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"title": entry.title},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            if user_input[CONF_MODE] == MODE_FAVORITE:
                return await self.async_step_favorite()
            return await self.async_step_radius()

        schema = vol.Schema(
            {
                vol.Required(CONF_MODE, default=MODE_RADIUS): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=MODE_RADIUS, label=t("mode_radius", self.hass)),
                            SelectOptionDict(value=MODE_FAVORITE, label=t("mode_favorite", self.hass)),
                        ],
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_radius(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """A live-filterable overview of the nearest stations around a
        location. Also validates the API key with a real request."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            lat = user_input[CONF_LATITUDE]
            lon = user_input[CONF_LONGITUDE]
            radius = user_input[CONF_RADIUS_KM]
            try:
                await async_fetch_stations(self.hass, api_key, lat, lon, radius)
            except Exception as err:  # noqa: BLE001 - map to form errors
                errors["base"] = "invalid_auth" if is_auth_error(err) else "cannot_connect"
            else:
                await self.async_set_unique_id(f"radius_{round(lat, 2)}_{round(lon, 2)}_{radius}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=t("device_name", self.hass, radius=radius),
                    data={
                        CONF_ENTRY_TYPE: ENTRY_TYPE_RADIUS,
                        CONF_API_KEY: api_key,
                        CONF_LATITUDE: lat,
                        CONF_LONGITUDE: lon,
                        CONF_RADIUS_KM: radius,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): _API_KEY_SELECTOR,
                vol.Required(CONF_LATITUDE, default=self.hass.config.latitude): vol.Coerce(float),
                vol.Required(CONF_LONGITUDE, default=self.hass.config.longitude): vol.Coerce(float),
                vol.Required(CONF_RADIUS_KM, default=DEFAULT_RADIUS_KM): _RADIUS_SELECTOR,
            }
        )
        return self.async_show_form(step_id="radius", data_schema=schema, errors=errors)

    async def async_step_favorite(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Pin one charge point or site as a favorite — either by its EvseID
        directly (e.g. from the sticker on the charger), or by searching near
        a location and picking from a list on the next screen."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._api_key = (user_input.get(CONF_API_KEY) or "").strip()
            station_id = (user_input.get(CONF_STATION_ID) or "").strip()
            if station_id:
                try:
                    station_json = await async_find_station_by_evse_id(self.hass, self._api_key, station_id)
                except Exception as err:  # noqa: BLE001 - map to form errors
                    errors["base"] = "invalid_auth" if is_auth_error(err) else "cannot_connect"
                else:
                    if station_json is None:
                        errors[CONF_STATION_ID] = "station_not_found"
                    else:
                        points = station_json.get("points") or []
                        connectors = {
                            p["evse_id"]: p
                            for p in (_parse_point(station_json, pt) for pt in points)
                            if p is not None
                        }
                        target = connectors.get(station_id)
                        if target is None:
                            errors[CONF_STATION_ID] = "station_not_found"
                        elif len(connectors) > 1:
                            groups = group_by_location(connectors)
                            self._pending_station = target
                            self._pending_site = next(iter(groups.values()))
                            self._pending_name = user_input.get(CONF_FAVORITE_NAME)
                            self._favorite_candidates = connectors
                            return await self.async_step_favorite_scope()
                        else:
                            self._favorite_candidates = connectors
                            self._confirm_station = target
                            self._confirm_location_id = None
                            return await self.async_step_favorite_confirm()
            else:
                lat = user_input[CONF_LATITUDE]
                lon = user_input[CONF_LONGITUDE]
                radius = user_input[CONF_RADIUS_KM]
                try:
                    stations = await async_fetch_stations(self.hass, self._api_key, lat, lon, radius)
                except Exception as err:  # noqa: BLE001 - map to form errors
                    errors["base"] = "invalid_auth" if is_auth_error(err) else "cannot_connect"
                else:
                    stations = apply_filters(
                        stations,
                        user_input.get(CONF_MIN_POWER_KW, DEFAULT_MIN_POWER_KW),
                        user_input.get(CONF_PLUG_TYPE, FILTER_ALL),
                        FILTER_ALL,
                        FILTER_ALL,
                    )
                    if not stations:
                        errors["base"] = "no_stations_found"
                    else:
                        self._favorite_candidates = stations
                        return await self.async_step_favorite_pick()

        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): _API_KEY_SELECTOR,
                vol.Optional(CONF_STATION_ID, default=""): str,
                vol.Optional(CONF_LATITUDE, default=self.hass.config.latitude): vol.Coerce(float),
                vol.Optional(CONF_LONGITUDE, default=self.hass.config.longitude): vol.Coerce(float),
                vol.Optional(CONF_RADIUS_KM, default=DEFAULT_RADIUS_KM): _RADIUS_SELECTOR,
                vol.Optional(CONF_MIN_POWER_KW, default=DEFAULT_MIN_POWER_KW): vol.Coerce(float),
                vol.Optional(CONF_PLUG_TYPE, default=FILTER_ALL): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=FILTER_ALL, label=t("option_all", self.hass)),
                            *(SelectOptionDict(value=p, label=p) for p in KNOWN_PLUG_TYPES),
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="favorite", data_schema=schema, errors=errors)

    async def async_step_favorite_scope(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """The entered EvseID names one charge point at a multi-connector
        site — ask whether to pin just that charge point or the whole site."""
        site = self._pending_site
        station = self._pending_station
        if site is None or station is None:
            return await self.async_step_favorite()

        if user_input is not None:
            name = user_input.get(CONF_FAVORITE_NAME) or self._pending_name
            if user_input[CONF_FAVORITE_SCOPE] == SCOPE_SITE:
                return await self._create_favorite_location_entry(site["location_id"], name)
            return await self._create_favorite_entry(station, name)

        count = site.get("count_total") or len(site["connectors"])
        schema = vol.Schema(
            {
                vol.Required(CONF_FAVORITE_SCOPE, default=SCOPE_SITE): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=SCOPE_SITE, label=t("favorite_scope_site", self.hass, count=count)
                            ),
                            SelectOptionDict(value=SCOPE_SINGLE, label=t("favorite_scope_single", self.hass)),
                        ],
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(CONF_FAVORITE_NAME, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="favorite_scope",
            data_schema=schema,
            description_placeholders={
                "site": site.get("station_name") or f"{site.get('street') or ''} {site.get('city') or ''}".strip(),
                "count": str(count),
                "evse_id": station.get("evse_id") or "",
            },
        )

    async def async_step_favorite_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Confirm an ID-resolved favorite before creating it — shows what
        was found and offers an optional custom name."""
        if self._confirm_station is None and self._confirm_location_id is None:
            return await self.async_step_favorite()

        if user_input is not None:
            name = user_input.get(CONF_FAVORITE_NAME)
            if self._confirm_station is not None:
                return await self._create_favorite_entry(self._confirm_station, name)
            return await self._create_favorite_location_entry(self._confirm_location_id, name)

        station = self._confirm_station or {}
        desc_parts = [p for p in [station.get("station_name"), station.get("city")] if p]
        what = t(
            "favorite_confirm_single",
            self.hass,
            evse_id=station.get("evse_id") or "?",
            name=" · ".join(desc_parts),
        )
        schema = vol.Schema({vol.Optional(CONF_FAVORITE_NAME, default=""): str})
        return self.async_show_form(
            step_id="favorite_confirm",
            data_schema=schema,
            description_placeholders={"what": what},
        )

    async def async_step_favorite_pick(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Pick either a single charge point or a whole site (all its
        connectors together) from the nearby search results gathered in the
        previous step."""
        if user_input is not None:
            selected = user_input[CONF_STATION_ID]
            if selected.startswith(_LOCATION_PREFIX):
                return await self._create_favorite_location_entry(
                    selected[len(_LOCATION_PREFIX) :], user_input.get(CONF_FAVORITE_NAME)
                )
            station = self._favorite_candidates.get(selected)
            if station is None:
                return await self.async_step_favorite()
            return await self._create_favorite_entry(station, user_input.get(CONF_FAVORITE_NAME))

        locations = group_by_location(self._favorite_candidates)
        location_options = [
            SelectOptionDict(
                value=f"{_LOCATION_PREFIX}{location_id}", label=location_display_label(location, self.hass)
            )
            for location_id, location in sorted(
                locations.items(), key=lambda kv: kv[1].get("distance_km") or 0
            )
        ]
        connector_options = [
            SelectOptionDict(value=station_id, label=station_display_label(station, self.hass))
            for station_id, station in sorted(
                self._favorite_candidates.items(), key=lambda kv: kv[1].get("distance_km") or 0
            )
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_STATION_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=[*location_options, *connector_options], mode=SelectSelectorMode.DROPDOWN, sort=False
                    )
                ),
                vol.Optional(CONF_FAVORITE_NAME, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="favorite_pick",
            data_schema=schema,
            description_placeholders={"count": str(len(connector_options))},
        )

    async def _create_favorite_entry(self, station: dict, custom_name: str | None) -> FlowResult:
        evse_id = station.get("evse_id")
        await self.async_set_unique_id(f"favorite_{evse_id}")
        self._abort_if_unique_id_configured()

        custom_name = (custom_name or "").strip()
        name = custom_name or station.get("station_name") or station.get("city") or evse_id
        return self.async_create_entry(
            title=t("favorite_device_name", self.hass, name=name),
            data={
                CONF_ENTRY_TYPE: ENTRY_TYPE_FAVORITE,
                CONF_API_KEY: self._api_key,
                CONF_STATION_ID: evse_id,
                CONF_FAVORITE_NAME: custom_name or None,
            },
        )

    async def _create_favorite_location_entry(self, location_id: str, custom_name: str | None) -> FlowResult:
        location = group_by_location(self._favorite_candidates).get(location_id)

        await self.async_set_unique_id(f"favorite_location_{location_id}")
        self._abort_if_unique_id_configured()

        custom_name = (custom_name or "").strip()
        fallback = (location.get("station_name") or location.get("city") or location_id) if location else location_id
        name = custom_name or fallback
        return self.async_create_entry(
            title=t("favorite_location_device_name", self.hass, name=name),
            data={
                CONF_ENTRY_TYPE: ENTRY_TYPE_FAVORITE_LOCATION,
                CONF_API_KEY: self._api_key,
                CONF_STATION_LOCATION_ID: location_id,
                CONF_FAVORITE_NAME: custom_name or None,
            },
        )


class LadestellenAtOptionsFlow(OptionsFlow):
    """Configure dialog. Radius entries only: pick which plug types get a
    dedicated available-count sensor. The live filters (min power, plug type,
    status, operator) intentionally stay on their number/select entities —
    they are meant to be flipped from a dashboard, not buried in a dialog."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if self.config_entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_RADIUS) != ENTRY_TYPE_RADIUS:
            return self.async_abort(reason="no_options")

        if user_input is not None:
            # Merge instead of replace — entry.options also carries the live
            # filter values written by the number/select entities.
            return self.async_create_entry(
                data={
                    **self.config_entry.options,
                    CONF_PLUG_TYPE_SENSORS: user_input.get(CONF_PLUG_TYPE_SENSORS, []),
                }
            )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_PLUG_TYPE_SENSORS,
                    default=list(self.config_entry.options.get(CONF_PLUG_TYPE_SENSORS) or []),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=p, label=p) for p in KNOWN_PLUG_TYPES],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                        sort=False,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
