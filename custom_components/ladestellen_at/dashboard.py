"""Automatic setup of the charging-stations dashboard with a pre-configured map card.

Uses Home Assistant's internal Lovelace storage API (there is no officially
documented integration API for this). Purely additive and idempotent — once
a dashboard has been created, this code never touches it again.
"""
from __future__ import annotations

import logging

from homeassistant.components import frontend
from homeassistant.components.lovelace import dashboard as ll_dashboard
from homeassistant.components.lovelace.const import (
    CONF_ALLOW_SINGLE_WORD,
    CONF_ICON,
    CONF_REQUIRE_ADMIN,
    CONF_SHOW_IN_SIDEBAR,
    CONF_TITLE,
    CONF_URL_PATH,
    DOMAIN as LOVELACE_DOMAIN,
    LOVELACE_DATA,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
import voluptuous as vol

from .const import DOMAIN
from .localization import t

_LOGGER = logging.getLogger(__name__)

DASHBOARD_URL_PATH = "ladestationen"
DASHBOARD_ICON = "mdi:ev-station"
FAVORITES_VIEW_PATH = "favoriten"
# Legacy marker key (pre-1.6.5): a foreign key inside the card dict made
# Home Assistant's visual editor refuse the card ("editor not supported").
# Still recognized for migration; new cards are identified via the entity
# mapping in the persistent store instead and stay marker-free.
CARD_ENTRY_ID_KEY = "ladestellen_at_entry_id"

# Persistent store, two jobs: (1) one-shot dashboard-created marker — if the
# user deletes the auto-created dashboard, that choice sticks (removing its
# sidebar entry additionally needs one restart, a Home Assistant limitation
# for integration-registered panels); (2) "cards" map entry_id -> entity_id
# of the card's first (custom) card, used to find our card in the Favorites
# view without polluting the card config.
_MARKER_STORE_VERSION = 1
_MARKER_STORE_KEY = f"{DOMAIN}.dashboard"


def _card_entity(card: dict) -> str | None:
    """The entity of a card's first stacked (custom) card — our identity key."""
    inner = card.get("cards")
    if isinstance(inner, list) and inner and isinstance(inner[0], dict):
        entity = inner[0].get("entity")
        if isinstance(entity, str) and str(inner[0].get("type", "")).startswith("custom:"):
            return entity
    return None


def _merge_preserving_user_edits(existing: dict, new: dict) -> dict:
    """Take the freshly generated card (current connector/entity lists) but
    keep whatever the user customized on the bundled custom card — e.g. a
    plug-type filter or changed title set via the visual editor. The restart
    upsert used to replace the whole card, silently dropping those edits
    (user report, 2026-07-19). Only `type` and `entity` are forced from the
    fresh card; the entities card below stays fully integration-managed so
    connector changes keep syncing."""
    existing_inner = existing.get("cards")
    new_inner = new.get("cards")
    if not (isinstance(existing_inner, list) and isinstance(new_inner, list) and new_inner):
        return new
    if not (
        existing_inner
        and isinstance(existing_inner[0], dict)
        and isinstance(new_inner[0], dict)
        and str(existing_inner[0].get("type", "")).startswith("custom:")
    ):
        return new
    merged_inner = list(new_inner)
    merged_inner[0] = {
        **existing_inner[0],
        "type": new_inner[0].get("type"),
        "entity": new_inner[0].get("entity"),
    }
    merged_inner[0].pop(CARD_ENTRY_ID_KEY, None)  # never re-inherit the legacy marker
    return {**new, "cards": merged_inner}


async def async_ensure_dashboard(hass: HomeAssistant) -> None:
    """Create the charging-stations dashboard if it doesn't exist yet (idempotent)."""
    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None:
        _LOGGER.warning(
            "Lovelace data not available — could not automatically set up the "
            "charging stations dashboard. Please create it manually."
        )
        return

    store: Store = Store(hass, _MARKER_STORE_VERSION, _MARKER_STORE_KEY)
    marker = await store.async_load()

    if DASHBOARD_URL_PATH in lovelace_data.dashboards:
        # Already exists — don't overwrite (respect any user edits). Also
        # backfill the marker for installations whose dashboard was created
        # before the marker existed. Preserve the rest of the store (cards map).
        if not (marker and marker.get("created")):
            await store.async_save({**(marker or {}), "created": True})
        return

    if marker and marker.get("created"):
        # Auto-created once before but missing now — the user deleted it.
        # Respect that and never re-create it.
        _LOGGER.debug("Charging stations dashboard was deleted by the user — not re-creating it")
        return

    title = t("dashboard_title", hass)

    dashboards_collection = ll_dashboard.DashboardsCollection(hass)
    await dashboards_collection.async_load()

    try:
        item = await dashboards_collection.async_create_item(
            {
                CONF_URL_PATH: DASHBOARD_URL_PATH,
                CONF_TITLE: title,
                CONF_ICON: DASHBOARD_ICON,
                CONF_SHOW_IN_SIDEBAR: True,
                CONF_REQUIRE_ADMIN: False,
                CONF_ALLOW_SINGLE_WORD: True,
            }
        )
    except (HomeAssistantError, vol.Invalid) as err:
        _LOGGER.warning("Could not create the charging stations dashboard: %s", err)
        return

    view_config = {
        "views": [
            {
                "title": title,
                "path": "ladestationen",
                "type": "panel",
                "cards": [
                    {
                        "type": "map",
                        "title": t("map_card_title", hass),
                        "geo_location_sources": [
                            {
                                "source": DOMAIN,
                                "label_mode": "attribute",
                                "attribute": "status",
                            }
                        ],
                        "default_zoom": 11,
                    }
                ],
            }
        ]
    }

    storage = ll_dashboard.LovelaceStorage(hass, item)
    lovelace_data.dashboards[DASHBOARD_URL_PATH] = storage
    await storage.async_save(view_config)

    frontend.async_register_built_in_panel(
        hass,
        LOVELACE_DOMAIN,
        frontend_url_path=DASHBOARD_URL_PATH,
        require_admin=False,
        show_in_sidebar=True,
        sidebar_title=title,
        sidebar_icon=DASHBOARD_ICON,
        config={"mode": "storage"},
        update=False,
    )

    await store.async_save({**(marker or {}), "created": True})
    _LOGGER.info("Charging stations dashboard set up automatically at /%s", DASHBOARD_URL_PATH)


async def async_add_location_card(hass: HomeAssistant, entry: ConfigEntry, card: dict) -> None:
    """Add or refresh the pre-filled Entities card for one favorite (single
    connector or whole site) in the dashboard's "Favorites" view (creating
    that view if needed), so its status/power/plug type/operator/ID sensors
    are visible together without manually building a card. Upserts by the
    entity of the card's first stacked card (mapping kept in the persistent
    store) — re-running this (e.g. every restart) keeps the card's entity
    list in sync as connectors appear/disappear, without duplicating it or
    touching any other card the user has added to the same view. The card
    config itself stays free of foreign keys so Home Assistant's visual
    editor keeps working on it; cards from before 1.6.5 carrying the legacy
    marker key are migrated to the clean form on first sync."""
    await async_ensure_dashboard(hass)

    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None or DASHBOARD_URL_PATH not in lovelace_data.dashboards:
        return

    storage = lovelace_data.dashboards[DASHBOARD_URL_PATH]
    try:
        config = await storage.async_load(False)
    except HomeAssistantError:
        return

    views = config.setdefault("views", [])
    favorites_view = next((v for v in views if v.get("path") == FAVORITES_VIEW_PATH), None)
    if favorites_view is None:
        favorites_view = {
            "title": t("favorites_view_title", hass),
            "path": FAVORITES_VIEW_PATH,
            "type": "masonry",
            "cards": [],
        }
        views.append(favorites_view)

    store: Store = Store(hass, _MARKER_STORE_VERSION, _MARKER_STORE_KEY)
    data = await store.async_load() or {}
    card_map: dict = data.setdefault("cards", {})

    new_entity = _card_entity(card)
    known_entities = {e for e in (card_map.get(entry.entry_id), new_entity) if e}

    def _is_ours(existing: dict) -> bool:
        if existing.get(CARD_ENTRY_ID_KEY) == entry.entry_id:
            return True  # legacy marker (pre-1.6.5)
        existing_entity = _card_entity(existing)
        return existing_entity is not None and existing_entity in known_entities

    cards = favorites_view.setdefault("cards", [])
    existing_index = next((i for i, c in enumerate(cards) if isinstance(c, dict) and _is_ours(c)), None)
    if existing_index is None:
        cards.append(card)
    else:
        cards[existing_index] = _merge_preserving_user_edits(cards[existing_index], card)

    await storage.async_save(config)
    if new_entity and card_map.get(entry.entry_id) != new_entity:
        card_map[entry.entry_id] = new_entity
        await store.async_save(data)
    _LOGGER.info("Synced favorites-dashboard card for entry %s", entry.entry_id)


_CARD_TYPES = ("custom:austrian-charging-stations-card", "custom:austrian-charging-stations-card")


async def async_remove_orphan_cards(hass: HomeAssistant) -> None:
    """Remove favorite cards whose sensor no longer exists (once per start).

    The per-delete cleanup hook (async_remove_entry -> remove_location_card)
    runs exactly once at deletion time — if Lovelace isn't ready at that
    moment (e.g. the favorite is deleted right after a restart), it silently
    no-ops and the card lingers forever (user report, 2026-07-19). This
    sweep catches such leftovers: any card in the Favorites view whose first
    stacked card is ours but references an entity that is gone from both the
    entity registry and the state machine gets dropped. Registry entries of
    a deleted config entry are removed immediately, so a still-loading
    favorite can't be mistaken for an orphan."""
    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None or DASHBOARD_URL_PATH not in lovelace_data.dashboards:
        return

    storage = lovelace_data.dashboards[DASHBOARD_URL_PATH]
    try:
        config = await storage.async_load(False)
    except HomeAssistantError:
        return

    views = (config or {}).get("views", [])
    favorites_view = next((v for v in views if v.get("path") == FAVORITES_VIEW_PATH), None)
    if favorites_view is None:
        return

    registry = er.async_get(hass)

    def _is_orphan(card: dict) -> bool:
        inner = card.get("cards")
        if not (isinstance(inner, list) and inner and isinstance(inner[0], dict)):
            return False
        if inner[0].get("type") not in _CARD_TYPES:
            return False
        entity = inner[0].get("entity")
        if not isinstance(entity, str) or not entity:
            return False
        return registry.async_get(entity) is None and hass.states.get(entity) is None

    cards = favorites_view.get("cards", [])
    remaining = [c for c in cards if not (isinstance(c, dict) and _is_orphan(c))]
    if len(remaining) == len(cards):
        return

    removed = len(cards) - len(remaining)
    if remaining:
        favorites_view["cards"] = remaining
    else:
        views.remove(favorites_view)
    await storage.async_save(config)
    _LOGGER.info("Removed %d orphaned favorites-dashboard card(s)", removed)


async def async_remove_location_card(hass: HomeAssistant, entry_id: str) -> None:
    """Remove a favorite's card from the Favorites view when its config entry
    is deleted, so the dashboard doesn't accumulate stale cards for favorites
    that no longer exist. Also drops the Favorites view itself if it ends up
    empty. No-op if the dashboard, view, or card is already gone (e.g. the
    user removed it manually)."""
    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None or DASHBOARD_URL_PATH not in lovelace_data.dashboards:
        return

    store: Store = Store(hass, _MARKER_STORE_VERSION, _MARKER_STORE_KEY)
    data = await store.async_load() or {}
    card_map: dict = data.get("cards") or {}
    stored_entity = card_map.pop(entry_id, None)
    if stored_entity is not None:
        data["cards"] = card_map
        await store.async_save(data)

    storage = lovelace_data.dashboards[DASHBOARD_URL_PATH]
    try:
        config = await storage.async_load(False)
    except HomeAssistantError:
        return

    views = config.get("views", [])
    favorites_view = next((v for v in views if v.get("path") == FAVORITES_VIEW_PATH), None)
    if favorites_view is None:
        return

    def _is_ours(card: dict) -> bool:
        if card.get(CARD_ENTRY_ID_KEY) == entry_id:
            return True  # legacy marker (pre-1.6.5)
        return stored_entity is not None and _card_entity(card) == stored_entity

    cards = favorites_view.get("cards", [])
    remaining = [c for c in cards if not (isinstance(c, dict) and _is_ours(c))]
    if len(remaining) == len(cards):
        return  # nothing matched — already removed or never added

    if remaining:
        favorites_view["cards"] = remaining
    else:
        views.remove(favorites_view)

    await storage.async_save(config)
    _LOGGER.info("Removed favorites-dashboard card for entry %s", entry_id)
