# Changelog

## 1.2.1 — 2026-08-29

- **New: strategy options.** Keys under `strategy:` are now honoured — `map: false`, `title`, `max_columns`. Unknown keys are ignored, so a typo cannot break the dashboard.
- **New: view strategy.** Next to the dashboard strategy the card file now also registers `ll-strategy-view-...`, so a single view of your own dashboard can be filled by the strategy (`views: - strategy: {type: custom:...}`) while every other view stays hand-editable. Until now the only way to change anything was *Take control*, which permanently stops the dashboard from following your config entries.
- No changes to the integration itself; only the bundled card file and the README.

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
