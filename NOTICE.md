# Data Source & Attribution

This integration retrieves charging-station data at runtime from the official Austrian national charging point directory **ladestellen.at** (Ladestellenverzeichnis), operated by **E-Control** (Energie-Control Austria).

The directory's public API is free to use but requires a personal API key, obtained by registering at https://admin.ladestellen.at/#/api/registrieren and accepting E-Control's terms of use. Each user of this integration registers their own key; the integration ships no key of its own.

**Attribution:** *Data: ladestellen.at (E-Control Austria)*

This integration fulfills that requirement by setting the `attribution` attribute (`"Data: ladestellen.at (E-Control Austria)"`) on every entity it creates, which Home Assistant surfaces in the entity's "More Info" dialog. If you build dashboards, automations, or republish this data elsewhere, please keep that attribution visible or add your own equivalent notice.

This integration is unofficial and not affiliated with, endorsed by, or supported by E-Control or ladestellen.at. It only reads the directory's published data via the official public API, in accordance with the API terms accepted at registration.
