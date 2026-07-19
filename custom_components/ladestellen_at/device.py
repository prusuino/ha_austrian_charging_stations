"""Shared device info for all platforms."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    CONF_ENTRY_TYPE,
    CONF_RADIUS_KM,
    DOMAIN,
    ENTRY_TYPE_FAVORITE,
    ENTRY_TYPE_FAVORITE_LOCATION,
)
from .localization import t


def device_info(hass: HomeAssistant, entry: ConfigEntry) -> DeviceInfo:
    entry_type = entry.data.get(CONF_ENTRY_TYPE)
    if entry_type == ENTRY_TYPE_FAVORITE:
        name = entry.title
        model = "Charging infrastructure data (WFS) — single favorite charge point"
    elif entry_type == ENTRY_TYPE_FAVORITE_LOCATION:
        name = entry.title
        model = "Charging infrastructure data (WFS) — favorite site (all charge points)"
    else:
        name = t("device_name", hass, radius=entry.data.get(CONF_RADIUS_KM))
        model = "Charging infrastructure data (WFS)"
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=name,
        manufacturer="ladestellen.at (E-Control Austria)",
        model=model,
        entry_type="service",
    )
