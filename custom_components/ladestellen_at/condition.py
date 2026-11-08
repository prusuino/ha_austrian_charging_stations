"""Conditions for Austrian Charging Stations.

"Favorite station is available" and "Favorite station is occupied" for the
automation editor's condition list, based on the derived site status of a
favorite (a single charge point or a whole site). As with the triggers, a
target that expands to other entities is narrowed to the favorite sensors
this integration created.

The condition platform API arrived in Home Assistant 2026.11. On an older
version the module still imports, but offers no conditions.
"""
from __future__ import annotations

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

try:
    from homeassistant.helpers.automation import DomainSpec
    from homeassistant.helpers.condition import Condition, make_entity_state_condition
except ImportError:  # Home Assistant before 2026.11: no condition platform API
    CONDITIONS: dict = {}
else:
    SITE_STATUS_SPECS = {SENSOR_DOMAIN: DomainSpec(value_source="site_status")}

    def _own_favorites(hass: HomeAssistant, entities: set[str]) -> set[str]:
        """Keep only the favorite status / site sensors of this integration."""
        registry = er.async_get(hass)
        return {
            entity_id
            for entity_id in entities
            if (entry := registry.async_get(entity_id)) is not None
            and entry.platform == DOMAIN
            and (entry.unique_id or "").endswith(("_status", "_available"))
        }

    class _IsFavoriteAvailable(make_entity_state_condition(SITE_STATUS_SPECS, "available")):
        """The favorite is available."""

        def entity_filter(self, entities: set[str]) -> set[str]:
            return _own_favorites(self._hass, super().entity_filter(entities))

    class _IsFavoriteOccupied(make_entity_state_condition(SITE_STATUS_SPECS, "occupied")):
        """The favorite is occupied."""

        def entity_filter(self, entities: set[str]) -> set[str]:
            return _own_favorites(self._hass, super().entity_filter(entities))

    CONDITIONS: dict[str, type[Condition]] = {
        "is_favorite_available": _IsFavoriteAvailable,
        "is_favorite_occupied": _IsFavoriteOccupied,
    }


async def async_get_conditions(hass: HomeAssistant) -> dict:
    """Return the conditions of this integration."""
    return CONDITIONS
