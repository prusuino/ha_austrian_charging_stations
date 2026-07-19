"""ladestellen.at Charging Stations – Home Assistant Integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_FAVORITE, ENTRY_TYPE_FAVORITE_LOCATION
from .coordinator import FavoriteLocationCoordinator, FavoriteStationCoordinator, LadestellenAtCoordinator
from .dashboard import async_ensure_dashboard, async_remove_location_card, async_remove_orphan_cards
from .frontend import async_register_card

_ORPHAN_SWEEP_SCHEDULED = f"{DOMAIN}_orphan_sweep_scheduled"

_LOGGER = logging.getLogger(__name__)

PLATFORMS_RADIUS = ["sensor", "geo_location", "number", "select"]
PLATFORMS_FAVORITE = ["sensor"]


def _is_radius(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_ENTRY_TYPE, "radius") == "radius"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    try:
        integration = await async_get_integration(hass, DOMAIN)
        await async_register_card(hass, str(integration.version))
    except Exception:  # noqa: BLE001 - card registration must never block integration setup
        _LOGGER.exception("Automatic registration of the bundled Lovelace card failed")

    if not hass.data.get(_ORPHAN_SWEEP_SCHEDULED):
        # Once per start, after everything is loaded: drop favorite cards
        # whose favorite no longer exists — the per-delete cleanup can miss
        # if Lovelace wasn't ready at deletion time.
        hass.data[_ORPHAN_SWEEP_SCHEDULED] = True

        async def _sweep(_event) -> None:
            try:
                await async_remove_orphan_cards(hass)
            except Exception:  # noqa: BLE001 - cleanup must never break startup
                _LOGGER.exception("Orphaned favorites-dashboard card sweep failed")

        if hass.is_running:
            hass.async_create_task(_sweep(None))
        else:
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _sweep)

    entry_type = entry.data.get(CONF_ENTRY_TYPE)
    if entry_type == ENTRY_TYPE_FAVORITE:
        coordinator = FavoriteStationCoordinator(hass, entry)
    elif entry_type == ENTRY_TYPE_FAVORITE_LOCATION:
        coordinator = FavoriteLocationCoordinator(hass, entry)
    else:
        coordinator = LadestellenAtCoordinator(hass, entry)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    is_radius = _is_radius(entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_RADIUS if is_radius else PLATFORMS_FAVORITE)

    if not is_radius:
        return True

    # The number/select filter entities write to entry.options — refresh
    # immediately on any change instead of waiting for the next 5-minute poll.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    try:
        await async_ensure_dashboard(hass)
    except Exception:  # noqa: BLE001 - dashboard setup must never block integration setup
        _LOGGER.exception("Automatic setup of the charging stations dashboard failed")

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    coordinator: LadestellenAtCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    platforms = PLATFORMS_RADIUS if _is_radius(entry) else PLATFORMS_FAVORITE
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Called when the config entry itself is deleted (not just unloaded) —
    clean up the auto-added dashboard card for a favorite, if any."""
    if entry.data.get(CONF_ENTRY_TYPE) not in (ENTRY_TYPE_FAVORITE, ENTRY_TYPE_FAVORITE_LOCATION):
        return
    try:
        await async_remove_location_card(hass, entry.entry_id)
    except Exception:  # noqa: BLE001 - dashboard cleanup must never block entry removal
        _LOGGER.exception("Automatic favorites-dashboard card removal failed for entry %s", entry.entry_id)
