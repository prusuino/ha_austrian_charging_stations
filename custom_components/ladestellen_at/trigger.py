"""Triggers for Austrian Charging Stations.

"Favorite station became available", "Favorite station became occupied"
and "Available count crossed a threshold" for the automation editor's
trigger list. The status triggers watch the derived site status of a
favorite (a single charge point or a whole site: available / occupied /
closed / out_of_service), the count trigger the available-count sensors of
a radius overview or a site. A target that expands to other entities (a
device, an area) is narrowed to the ones this integration created.

The trigger platform API arrived in Home Assistant 2026.10. On an older
version the module still imports, but offers no triggers.
"""
from __future__ import annotations

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

try:
    from homeassistant.helpers.automation import DomainSpec
    from homeassistant.helpers.trigger import (
        Trigger,
        make_entity_numerical_state_crossed_threshold_trigger,
        make_entity_target_state_trigger,
    )
except ImportError:  # Home Assistant before 2026.10: no trigger platform API
    TRIGGERS: dict = {}
else:
    # The derived status lives in the site_status attribute of the favorite
    # sensors (status sensor of a charge point, available-count sensor of a
    # site); the available-count sensors carry the count as their state.
    SITE_STATUS_SPECS = {SENSOR_DOMAIN: DomainSpec(value_source="site_status")}
    COUNT_SPECS = {SENSOR_DOMAIN: DomainSpec()}

    def _own_entities(hass: HomeAssistant, entities: set[str], suffixes: tuple[str, ...]) -> set[str]:
        """Keep only entities created by this integration whose unique id
        ends with one of the suffixes (or contains it for the per-plug-type
        count sensors)."""
        registry = er.async_get(hass)
        kept: set[str] = set()
        for entity_id in entities:
            entry = registry.async_get(entity_id)
            if entry is None or entry.platform != DOMAIN:
                continue
            unique_id = entry.unique_id or ""
            if any(unique_id.endswith(s) or f"{s}_" in unique_id for s in suffixes):
                kept.add(entity_id)
        return kept

    class _FavoriteBecameAvailable(make_entity_target_state_trigger(SITE_STATUS_SPECS, "available")):
        """A favorite charge point or site becomes available."""

        def entity_filter(self, entities: set[str]) -> set[str]:
            return _own_entities(self._hass, super().entity_filter(entities), ("_status", "_available"))

    class _FavoriteBecameOccupied(make_entity_target_state_trigger(SITE_STATUS_SPECS, "occupied")):
        """A favorite charge point or site becomes occupied."""

        def entity_filter(self, entities: set[str]) -> set[str]:
            return _own_entities(self._hass, super().entity_filter(entities), ("_status", "_available"))

    class _AvailableCountCrossed(make_entity_numerical_state_crossed_threshold_trigger(COUNT_SPECS, None)):
        """The number of available charge points of a radius overview, a
        plug type or a site crosses the configured threshold."""

        def entity_filter(self, entities: set[str]) -> set[str]:
            return _own_entities(self._hass, super().entity_filter(entities), ("_free", "_available"))

    TRIGGERS: dict[str, type[Trigger]] = {
        "favorite_became_available": _FavoriteBecameAvailable,
        "favorite_became_occupied": _FavoriteBecameOccupied,
        "available_count_crossed_threshold": _AvailableCountCrossed,
    }


async def async_get_triggers(hass: HomeAssistant) -> dict:
    """Return the triggers of this integration."""
    return TRIGGERS
