# Changelog

## 1.0.0 — 2026-07-19

Initial release.

- **Radius overview**: the nearest charging sites around a location as live map markers (`geo_location`, one per site with an availability label), an available-count sensor, and live-adjustable filters (minimum power, plug family, status, operator) via number/select entities. Optional per-plug-family availability sensors via the Configure dialog.
- **Favorites**: pin a single charge point (by EvseID or via location search) or a whole site — status/power/plug/operator sensors per charge point, a derived site status (available / occupied / closed / out of service, evaluated against the registry's opening hours), per-plug-family availability sensors, and auto-generated dashboard cards.
- **Bundled Lovelace card** `austrian-charging-stations-card` with colored per-connector status boxes, availability badge, opening hours, a visible-plug-families filter, and a visual editor.
- **Automatic dashboard** with a full-screen map view and a favorites view.
- Data from the official Austrian charging point directory **ladestellen.at** (E-Control) with real-time per-charge-point status and ad-hoc prices; requires a free personal API key.
- Localized in German, English, French, and Italian.
