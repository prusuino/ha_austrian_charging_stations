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
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
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

    _GAP = (STATE_UNAVAILABLE, STATE_UNKNOWN)

    class _AcrossGaps:
        """Lets a target-state trigger see a change that follows a gap.

        The core skips every change out of "unavailable" or "unknown". While
        the source cannot be reached the entities are unavailable, so
        whatever began during the outage would switch an entity from
        unavailable straight to its new state, and the core would not fire.
        Such a change is let through here and judged against the last state
        seen before the gap (the tracked value, which can be an attribute): it
        fires when that value was not already the
        target. With nothing known from before the gap, a trigger that
        announces something (`_fire_without_history`) fires, the others
        stay quiet. The core asks is_valid_state for every state with a
        value and only then is_valid_transition, so the states are
        remembered there.
        """

        _excluded_from_states = frozenset()
        _fire_without_history = False

        def __init__(self, hass: HomeAssistant, config) -> None:
            super().__init__(hass, config)
            self._last_state: dict[str, str] = {}
            self._previous_state: dict[str, str | None] = {}

        def entity_filter(self, entities: set[str]) -> set[str]:
            # Start from what the entities show now, so that a gap right
            # after the automation starts is judged against a known state.
            kept = super().entity_filter(entities)
            for entity_id in kept:
                state = self._hass.states.get(entity_id)
                if entity_id not in self._last_state and state is not None and state.state not in _GAP:
                    self._last_state[entity_id] = self._get_tracked_value(state)
            return kept

        def is_valid_state(self, state, report_not_triggered) -> bool:
            self._previous_state[state.entity_id] = self._last_state.get(state.entity_id)
            self._last_state[state.entity_id] = self._get_tracked_value(state)
            return super().is_valid_state(state, report_not_triggered)

        def is_valid_transition(self, from_state, to_state) -> bool:
            if from_state.state not in _GAP:
                return super().is_valid_transition(from_state, to_state)
            before = self._previous_state.get(to_state.entity_id)
            if before is None:
                return self._fire_without_history
            return before not in self._to_states

    class _FavoriteBecameAvailable(_AcrossGaps, make_entity_target_state_trigger(SITE_STATUS_SPECS, "available")):
        """A favorite charge point or site becomes available; after an outage
        when it was not available before it."""

        def entity_filter(self, entities: set[str]) -> set[str]:
            return _own_entities(self._hass, super().entity_filter(entities), ("_status", "_available"))

    class _FavoriteBecameOccupied(_AcrossGaps, make_entity_target_state_trigger(SITE_STATUS_SPECS, "occupied")):
        """A favorite charge point or site becomes occupied; after an outage
        when it was not occupied before it."""

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
