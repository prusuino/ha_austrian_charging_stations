"""ladestellen.at Charging Stations – Home Assistant Integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_FAVORITE, ENTRY_TYPE_FAVORITE_LOCATION
from .issues import async_clear_entry_issues
from .coordinator import FavoriteLocationCoordinator, FavoriteStationCoordinator, LadestellenAtCoordinator

type LadestellenAtConfigEntry = ConfigEntry[LadestellenAtCoordinator | FavoriteStationCoordinator | FavoriteLocationCoordinator]
from .frontend import async_serve_card

_LOGGER = logging.getLogger(__name__)

PLATFORMS_RADIUS = ["sensor", "geo_location", "number", "select"]
PLATFORMS_FAVORITE = ["sensor"]


def _is_radius(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_ENTRY_TYPE, "radius") == "radius"


async def async_setup_entry(hass: HomeAssistant, entry: LadestellenAtConfigEntry) -> bool:
    try:
        await async_serve_card(hass)
    except Exception:  # noqa: BLE001 - serving the card must never block integration setup
        _LOGGER.exception("Serving the bundled Lovelace card failed")

    entry_type = entry.data.get(CONF_ENTRY_TYPE)
    if entry_type == ENTRY_TYPE_FAVORITE:
        coordinator = FavoriteStationCoordinator(hass, entry)
    elif entry_type == ENTRY_TYPE_FAVORITE_LOCATION:
        coordinator = FavoriteLocationCoordinator(hass, entry)
    else:
        coordinator = LadestellenAtCoordinator(hass, entry)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    is_radius = _is_radius(entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_RADIUS if is_radius else PLATFORMS_FAVORITE)

    if not is_radius:
        return True

    # The number/select filter entities write to entry.options — refresh
    # immediately on any change instead of waiting for the next 5-minute poll.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(hass: HomeAssistant, entry: LadestellenAtConfigEntry) -> None:
    await entry.runtime_data.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: LadestellenAtConfigEntry) -> bool:
    platforms = PLATFORMS_RADIUS if _is_radius(entry) else PLATFORMS_FAVORITE
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        async_clear_entry_issues(hass, entry.entry_id)
    return unloaded
