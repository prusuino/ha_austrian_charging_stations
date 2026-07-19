# Changelog

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
