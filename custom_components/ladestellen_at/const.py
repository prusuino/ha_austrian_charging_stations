"""Constants for the ladestellen.at integration."""
DOMAIN = "ladestellen_at"

# Official public API of the Austrian national charging point directory
# (Ladestellenverzeichnis), operated by E-Control. Free, but every user needs
# their own API key: https://admin.ladestellen.at/#/api/registrieren
API_BASE = "https://api.e-control.at/charge/1.0"

# The API validates the Referer header against the domain registered with the
# key. The documented setup instructs users to register the generic domain
# below, so the integration can send a fixed Referer for everyone.
API_REFERER = "https://homeassistant.local/"

UPDATE_INTERVAL_MINUTES = 5
FETCH_TIMEOUT_SECONDS = 60

# The proximity search endpoint returns the ~100 nearest stations. Radius
# filtering is applied client-side on top via the returned distance, so a
# radius entry covers at most the 100 nearest sites.
MAX_PROXIMITY_STATIONS = 100

CONF_API_KEY = "api_key"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_RADIUS_KM = "radius_km"

DEFAULT_RADIUS_KM = 15
MAX_RADIUS_KM = 100

# Cap on map markers created by one radius entry (the proximity endpoint
# already caps at ~100 stations, this is a second safety net).
MAX_MAP_MARKERS = 500

# Which kind of config entry this is. Radius entries (the default kind) show
# every station within a live-filterable area. Favorite entries pin exactly
# one specific charge point or whole station, independent of any radius.
CONF_ENTRY_TYPE = "entry_type"
ENTRY_TYPE_RADIUS = "radius"
ENTRY_TYPE_FAVORITE = "favorite"
ENTRY_TYPE_FAVORITE_LOCATION = "favorite_location"

# Favorite-specific config keys.
CONF_STATION_ID = "station_id"  # the charge point's EvseID (e.g. AT*DAE*E0002115)
CONF_FAVORITE_NAME = "favorite_name"

# Favorite-location-specific config key: "country/operator/stationId" — the
# three path segments needed to re-fetch a whole station (site) later.
# "/" is safe as separator: it cannot appear in any of the three parts.
CONF_STATION_LOCATION_ID = "station_location_id"

# Options (live-adjustable filters, stored in entry.options).
CONF_MIN_POWER_KW = "min_power_kw"
CONF_PLUG_TYPE = "plug_type"
CONF_STATUS = "status"
CONF_OPERATOR = "operator"

DEFAULT_MIN_POWER_KW = 0.0

# Canonical (language-independent) values stored in entry.options.
FILTER_ALL = "__all__"
STATUS_AVAILABLE = "available"
STATUS_OCCUPIED = "occupied"

# Option (set via the integration's Configure dialog): plug types that get
# their own dedicated available-count sensor on a radius entry.
CONF_PLUG_TYPE_SENSORS = "plug_type_sensors"

# The API reports connector types in two coexisting vocabularies (verified
# live against 1,000 Vienna stations on 2026-07-19): the legacy keys of the
# original Ladestellenverzeichnis (STYPE2, CCCS2, CG105, ...) and the newer
# OCPI-style keys (IEC_62196_T2, CHADEMO, DOMESTIC_F, ...). Both are
# normalized into these canonical plug families, which are what filters,
# sensors, and the card work with.
PLUG_FAMILY_TYPE2 = "Type 2"
PLUG_FAMILY_CCS = "CCS"
PLUG_FAMILY_CHADEMO = "CHAdeMO"
PLUG_FAMILY_TESLA = "Tesla"
PLUG_FAMILY_TYPE1 = "Type 1"
PLUG_FAMILY_HOUSEHOLD = "Household"
PLUG_FAMILY_CEE = "CEE / Industrial"
PLUG_FAMILY_OTHER = "Other"

KNOWN_PLUG_TYPES = [
    PLUG_FAMILY_TYPE2,
    PLUG_FAMILY_CCS,
    PLUG_FAMILY_CHADEMO,
    PLUG_FAMILY_TESLA,
    PLUG_FAMILY_TYPE1,
    PLUG_FAMILY_HOUSEHOLD,
    PLUG_FAMILY_CEE,
    PLUG_FAMILY_OTHER,
]

# Raw API connector key -> canonical plug family. Keys not listed here go
# through the prefix rules below and finally fall back to PLUG_FAMILY_OTHER.
PLUG_KEY_FAMILIES = {
    "STYPE2": PLUG_FAMILY_TYPE2,
    "CTYPE2": PLUG_FAMILY_TYPE2,
    "IEC_62196_T2": PLUG_FAMILY_TYPE2,
    "CCCS2": PLUG_FAMILY_CCS,
    "CCCS1": PLUG_FAMILY_CCS,
    "IEC_62196_T2_COMBO": PLUG_FAMILY_CCS,
    "IEC_62196_T1_COMBO": PLUG_FAMILY_CCS,
    "CG105": PLUG_FAMILY_CHADEMO,
    "CHADEMO": PLUG_FAMILY_CHADEMO,
    "CHAOJI": PLUG_FAMILY_CHADEMO,
    "GBT_AC": PLUG_FAMILY_CHADEMO,
    "GBT_DC": PLUG_FAMILY_CHADEMO,
    "CTESLA": PLUG_FAMILY_TESLA,
    "TESLA_S": PLUG_FAMILY_TESLA,
    "TESLA_R": PLUG_FAMILY_TESLA,
    "CTYPE1": PLUG_FAMILY_TYPE1,
    "IEC_62196_T1": PLUG_FAMILY_TYPE1,
    "SCEE-7-8": PLUG_FAMILY_HOUSEHOLD,
    "SBS1361": PLUG_FAMILY_HOUSEHOLD,
}

# Prefix rules for whole key families.
PLUG_KEY_PREFIX_FAMILIES = [
    ("DOMESTIC_", PLUG_FAMILY_HOUSEHOLD),
    ("NEMA_", PLUG_FAMILY_HOUSEHOLD),
    ("S309", PLUG_FAMILY_CEE),
    ("IEC_60309", PLUG_FAMILY_CEE),
]

# Compact entity_id suffix per plug family.
PLUG_TYPE_SLUGS = {
    PLUG_FAMILY_TYPE2: "type2",
    PLUG_FAMILY_CCS: "ccs",
    PLUG_FAMILY_CHADEMO: "chademo",
    PLUG_FAMILY_TESLA: "tesla",
    PLUG_FAMILY_TYPE1: "type1",
    PLUG_FAMILY_HOUSEHOLD: "household",
    PLUG_FAMILY_CEE: "cee",
    PLUG_FAMILY_OTHER: "other",
}

# Short display label per plug family (the family names are already short —
# identity mapping, kept for structural compatibility with the sensor layer).
PLUG_TYPE_SHORT_LABELS = {p: p for p in KNOWN_PLUG_TYPES}

# Raw API point status -> internal canonical status vocabulary used by all
# downstream logic (filters, sensors, card colors, localization). The raw AT
# value is kept alongside in the station dict as `at_status` for attributes.
# BLOCKED (vehicle parked without charging) and RESERVED both mean "you can't
# charge here right now" and map to Occupied.
AT_STATUS_MAP = {
    "AVAILABLE": "Available",
    "CHARGING": "Occupied",
    "RESERVED": "Occupied",
    "BLOCKED": "Occupied",
    "INOPERATIVE": "OutOfService",
    # Both spellings occur: OUTOFORDER in the search/point endpoints
    # (verified live 2026-07-19), OUT_OF_ORDER in the DATEX II publications.
    "OUTOFORDER": "OutOfService",
    "OUT_OF_ORDER": "OutOfService",
}
