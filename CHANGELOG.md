# Changelog

## 1.4.0 — 2026-08-29

- **New: re-authentication when the API key stops working.** If ladestellen.at rejects the stored key (revoked, expired, re-registered for another domain), the entry no longer just keeps retrying: Home Assistant now shows a re-authentication notification under Settings → Devices & services asking for the new key. The key is checked with the same request the entry makes on every refresh, stored, and the entry reloaded — location, radius, favorite and filter settings are untouched. Until now a rejected key looked exactly like an outage ("ladestellen.at unreachable") and the entities simply stayed unavailable with no way to enter a new key short of deleting the entry.
- **Changed: the API key is entered through a masked password field** in every dialog that asks for it (radius overview, favorite, re-authentication). It was a plain text field until now, showing the key in cleartext. The key is also **no longer prefilled** from an existing entry: each new entry asks for it — previously the stored key was copied visibly into every further setup dialog.
- **Changed: suggested entity ids carry a short per-entry suffix** — the last four characters of the config entry id, lower-case — next to the radius or the favorite's name: `sensor.at_charging_stations_available_15km_k7qa`, `select.at_charging_stations_status_15km_k7qa`, `sensor.at_charging_station_favorite_hauptbahnhof_linz_k7qa_power_kw`. The radius alone did not tell two entries at different locations with the same radius apart, and two favorite sites can share a name; Home Assistant resolved either by appending `_2` to whichever entry loaded second, so an automation copied between entries silently targeted the wrong one. A suggested id only applies when an entity is first created, so **the entities of existing entries keep the ids they have** — unique ids are unchanged, nothing is renamed, only newly added entries get the new form. The README entity tables show the pattern.
- Documented in the code why a map marker whose site leaves the radius keeps its entity registry row (the site usually comes back, and the row is what brings the marker back with the same entity id, its history and your changes to it), while a per-plug-type sensor you deselect in the Configure dialog is removed from the registry as well. Behaviour is unchanged.
- Internal: the coordinators now pass their config entry to Home Assistant explicitly (required by the re-authentication above, and by Home Assistant from 2025.12 in any case).

Nothing to do on your side when upgrading.

## 1.3.0 — 2026-08-29

- **Fixed: the map markers of two overlapping radius entries no longer collide.** A marker's unique id was the site's id alone, so when a second radius entry covered some of the same sites (a home and a work radius that overlap, say), Home Assistant rejected that entry's markers for the shared sites as duplicates — they were silently missing from its map, with nothing but a log line. The unique id now includes the config entry. **Existing markers are migrated automatically on the first start**: each radius entry's registry entries are moved to the new unique id in place, so entity ids, history, and anything you changed on a marker (name, visibility, area) are kept. Nothing to do on your side.
- **Fixed: map markers go unavailable while the data source is unreachable**, as the sensors already did. Until now a marker kept showing its last known label ("6/7 available") indefinitely, however stale the data behind it was.
- **Changed: the filter entities' suggested entity ids now carry the radius**, matching the availability sensor: `number.at_charging_stations_min_power_kw_15km`, `select.at_charging_stations_plug_type_15km`, `select.at_charging_stations_status_15km` and `select.at_charging_stations_operator_15km` for a 15 km entry. The previous fixed ids were shared by every radius entry, which Home Assistant resolved by appending `_2` to the second — an automation copied from one entry silently targeted the other. A suggested id only applies when an entity is first created, so **the entities of existing entries keep the ids they have**; only newly added radius entries get the new form.
- **New: strategy options.** Keys under `strategy:` are now honoured — `map: false`, `title`, `max_columns`. Unknown keys are ignored, so a typo cannot break the dashboard.
- **New: view strategy.** Next to the dashboard strategy the card file now also registers `ll-strategy-view-...`, so a single view of your own dashboard can be filled by the strategy (`views: - strategy: {type: custom:...}`) while every other view stays hand-editable. Until now the only way to change anything was *Take control*, which permanently stops the dashboard from following your config entries.
- Documented: a map marker's entity id follows its localized name (`geo_location.ladestation_…` on a German instance, `geo_location.charging_station_…` on an English one), so it differs between installations. The README now shows how to address markers independently of the language — the map card's `geo_location_sources` and the `source` attribute in templates.
- README: the installation instructions now describe the HACS default listing, with a My Home Assistant button that opens the integration directly in HACS.
- Changed: the minimum Home Assistant version is now **2025.4.0**. The strategy labels each map marker with its live availability (`label_mode: attribute` on the map card's geo-location sources), and that option only exists in the map card since 2025.4; on an older release the markers render without labels. The integration itself still runs on 2024.12, the floor states what the generated dashboard needs.

## 1.2.0 — 2026-08-29

- **Changed: the integration no longer creates a dashboard or writes cards into your Lovelace configuration.** It previously created a "Charging Stations AT" dashboard on first setup, kept an entities card per favorite in sync with it, and registered its own Lovelace resource. Dashboards, cards and resources are the user's configuration, and an integration should not be writing into them — so all of that is gone.
- **New: a dashboard strategy** as the replacement. A strategy is a recipe Home Assistant renders in the browser: it stores nothing, overwrites nothing, and reflects your current setup on every page load — add a favorite and its section appears, delete one and it is gone, with no stale card left behind. It ships inside the card file, so no extra resource is needed. Setup is in the README; in short, a new dashboard with:

  ```yaml
  strategy:
    type: custom:austrian-charging-stations
  views: []
  ```

  It renders a full-screen map of the sites in range (markers labeled with live availability), then the radius search with its filter entities, then one section per favorite with the bundled card and that favorite's headline sensors.
- **Upgrading:** nothing breaks and nothing is removed from your configuration. The dashboard and cards created by earlier versions stay exactly as they are — they are now plain dashboards of yours, to keep, edit or delete as you like, and no longer touched or re-synced on restart. The card resource registered by earlier versions also stays and keeps working, so the card continues to render; you only need the manual resource step (see README) for a fresh installation. The strategy is entirely optional.
- **Fixed: map markers you made visible by hand are no longer hidden again on every restart.** The old code re-applied its "hidden" flag to any entity that had none — which is exactly what unhiding one leaves behind, so the choice never survived a restart. New markers are still hidden by default so they do not flood Home Assistant's auto-generated overview map; existing ones are left alone.
- Changed: the minimum Home Assistant version is now **2024.12.0** (previously 2024.1.0), matching the APIs actually in use.
- Added: `translations/en.json`, so the config and options dialogs show proper English text instead of raw translation keys.

## 1.1.4 — 2026-07-19

- New: the card's header badges now stack vertically at a uniform size. A new **hide badges** multi-select in the visual editor (`hidden_badges` in YAML) hides individual ones.
- New: sites and charge points whose operator declares **renewable energy** show a dark-green leaf badge in the bundled card's header, next to the availability badge. For a whole site the badge appears only when no charge point explicitly reports otherwise. The per-charge-point flag is also part of the overview sensor's `connectors` attribute (`renewable`).

## 1.1.0 — 2026-07-19

- New: **ad-hoc price sensors** — every favorite charge point gets a dedicated price sensor (`..._price_ct_kwh`, in cents per kWh; 0 for explicitly free chargers), for single favorites and per charge point at favorite sites, listed on the auto-generated dashboard cards below the charging power.
- New: the favorite search picker shows the **price next to the distance** — single charge points with their own price ("· 52.8 ct/kWh"), whole sites with the cheapest one ("· from 44 ct/kWh") when their charge points differ.

## 1.0.0 — 2026-07-19

Initial release.

- **Radius overview**: the nearest charging sites around a location as live map markers (`geo_location`, one per site with an availability label), an available-count sensor, and live-adjustable filters (minimum power, plug family, status, operator) via number/select entities. Optional per-plug-family availability sensors via the Configure dialog.
- **Favorites**: pin a single charge point (by EvseID or via location search) or a whole site — status/power/plug/operator sensors per charge point, a derived site status (available / occupied / closed / out of service, evaluated against the registry's opening hours), per-plug-family availability sensors, and auto-generated dashboard cards.
- **Bundled Lovelace card** `austrian-charging-stations-card` with colored per-connector status boxes, availability badge, opening hours, a visible-plug-families filter, and a visual editor.
- **Automatic dashboard** with a full-screen map view and a favorites view.
- Data from the official Austrian charging point directory **ladestellen.at** (E-Control) with real-time per-charge-point status and ad-hoc prices; requires a free personal API key.
- Localized in German, English, French, and Italian.
