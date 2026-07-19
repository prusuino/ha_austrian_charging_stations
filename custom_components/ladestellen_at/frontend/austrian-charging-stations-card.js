/* austrian-charging-stations-card — colored per-connector status boxes for a
 * favorite charging station or a whole favorite site (green = available,
 * red = occupied, gray = out of service). Ships with the integration;
 * registered as a Lovelace resource automatically, no manual setup required.
 *
 * Config:
 *   type: custom:austrian-charging-stations-card
 *   entity: sensor.at_charging_station_favorite_location_xxx   (site summary)
 *     — or —
 *   entity: sensor.at_charging_station_favorite_xxx            (single station)
 *   title: optional override for the card header
 */

const STATUS_COLORS = {
  Available: "var(--success-color, #2e7d32)",
  Occupied: "var(--error-color, #c62828)",
  OutOfService: "var(--disabled-text-color, #757575)",
  // Pseudo status used while the whole site is outside its opening hours —
  // distinct from the gray "broken" look and the green/red live states.
  Closed: "var(--info-color, #1565c0)",
};
const UNKNOWN_COLOR = "var(--warning-color, #f9a825)";

const AVAILABLE_WORD = { de: "frei", en: "available", fr: "libre", it: "liberi" };

const CLOSED_TODAY_WORDS = {
  de: "Heute geschlossen",
  en: "Closed today",
  fr: "Fermé aujourd'hui",
  it: "Chiuso oggi",
};

const SITE_STATUS_WORDS = {
  closed: { de: "Geschlossen", en: "Closed", fr: "Fermée", it: "Chiusa" },
  out_of_service: {
    de: "Ausser Betrieb",
    en: "Out of service",
    fr: "Hors service",
    it: "Fuori servizio",
  },
};

const TYPE_WORD = { de: "Typ", en: "Type", fr: "Type", it: "Tipo" };
const CABLE_WORD = { de: "Kabel", en: "cable", fr: "câble", it: "cavo" };

/* Abbreviations stay short as long as they are unambiguous; only the two
 * Type-2 variants (socket vs. attached cable) need the distinction. */
function shortPlug(plug, lang) {
  if (!plug) return "";
  const type = TYPE_WORD[lang] || TYPE_WORD.en;
  if (plug === "Type 2") return `${type} 2`;
  if (plug === "Type 1") return `${type} 1`;
  if (plug === "CEE / Industrial") return "CEE";
  return plug;
}

function shortPlugs(plugs, lang) {
  if (!Array.isArray(plugs)) return "";
  const shorts = [...new Set(plugs.map((p) => shortPlug(p, lang)).filter(Boolean))];
  return shorts.join(" · ");
}

const EDITOR_LABELS = {
  entity: {
    de: "Favorit-Sensor (Standort oder einzelne Station)",
    en: "Favorite sensor (site or single station)",
    fr: "Capteur favori (site ou borne individuelle)",
    it: "Sensore preferito (sede o stazione singola)",
  },
  title: {
    de: "Titel (optional)",
    en: "Title (optional)",
    fr: "Titre (optionnel)",
    it: "Titolo (opzionale)",
  },
  plug_types: {
    de: "Sichtbare Steckertypen (leer = alle)",
    en: "Visible plug types (empty = all)",
    fr: "Types de prises visibles (vide = tous)",
    it: "Tipi di presa visibili (vuoto = tutti)",
  },
};

// All plug-type values observed in the national data set — offered in the
// editor when the selected entity's own connector list isn't available yet.
const KNOWN_PLUG_TYPES = [
  "Type 2",
  "CCS",
  "CHAdeMO",
  "Tesla",
  "Type 1",
  "Household",
  "CEE / Industrial",
  "Other",
];

class AustrianChargingStationsCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        const config = { type: "custom:austrian-charging-stations-card", ...ev.detail.value };
        if (!config.title) delete config.title;
        if (!Array.isArray(config.plug_types) || !config.plug_types.length) {
          delete config.plug_types;
        }
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config },
            bubbles: true,
            composed: true,
          })
        );
      });
      this.appendChild(this._form);
    }
    const lang = ((this._hass.language || "en").split("-")[0]);
    this._form.hass = this._hass;
    this._form.data = {
      entity: this._config.entity || "",
      title: this._config.title || "",
      plug_types: this._config.plug_types || [],
    };
    // Offer only the plug types that actually exist at the selected site
    // (falling back to the full national list before an entity is picked).
    const stateObj = this._hass.states[this._config.entity];
    const sitePlugs =
      stateObj && Array.isArray(stateObj.attributes.connectors)
        ? [
            ...new Set(
              stateObj.attributes.connectors.flatMap((c) =>
                Array.isArray(c.plug_types) ? c.plug_types : []
              )
            ),
          ]
        : [];
    const plugOptions = (sitePlugs.length ? sitePlugs : KNOWN_PLUG_TYPES).map(
      (value) => ({ value, label: shortPlug(value, lang) || value })
    );
    this._form.schema = [
      {
        name: "entity",
        required: true,
        selector: { entity: { domain: "sensor", integration: "ladestellen_at" } },
      },
      { name: "title", selector: { text: {} } },
      {
        name: "plug_types",
        selector: {
          select: { multiple: true, mode: "dropdown", options: plugOptions },
        },
      },
    ];
    this._form.computeLabel = (schema) =>
      (EDITOR_LABELS[schema.name] &&
        (EDITOR_LABELS[schema.name][lang] || EDITOR_LABELS[schema.name].en)) ||
      schema.name;
  }
}

customElements.define("austrian-charging-stations-card-editor", AustrianChargingStationsCardEditor);

class AustrianChargingStationsCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) {
      throw new Error("austrian-charging-stations-card: 'entity' is required");
    }
    this._config = config;
  }

  static async getConfigElement() {
    // ha-form and the selector components are lazy-loaded by the frontend;
    // loading a core card editor first guarantees they are registered.
    if (window.loadCardHelpers) {
      const helpers = await window.loadCardHelpers();
      const entitiesCard = await helpers.createCardElement({ type: "entities", entities: [] });
      if (entitiesCard && entitiesCard.constructor.getConfigElement) {
        await entitiesCard.constructor.getConfigElement();
      }
    }
    return document.createElement("austrian-charging-stations-card-editor");
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 3;
  }

  static getStubConfig(hass) {
    const candidate = Object.keys(hass.states).find((id) =>
      id.startsWith("sensor.at_charging_station_favorite")
    );
    return { entity: candidate || "" };
  }

  _lang() {
    const lang = (this._hass && this._hass.language) || "en";
    return lang.split("-")[0];
  }

  _render() {
    if (!this._hass || !this._config) return;
    const stateObj = this._hass.states[this._config.entity];

    if (!this._root) {
      this._root = this.attachShadow({ mode: "open" });
    }

    if (!stateObj) {
      this._root.innerHTML = `<ha-card><div style="padding:16px;">
        Entity not found: ${this._escape(this._config.entity)}</div></ha-card>`;
      return;
    }

    const attrs = stateObj.attributes;
    const isSite = Array.isArray(attrs.connectors);
    // Renewable-energy badge: shown when the operator declares green power —
    // for a site only when no connector explicitly says otherwise.
    let renewable;
    if (isSite) {
      const flags = attrs.connectors.map((c) => c.renewable);
      renewable = flags.some((f) => f === true) && !flags.some((f) => f === false);
    } else {
      renewable = attrs.green_energy === true;
    }
    const title =
      this._config.title || attrs.friendly_name || this._config.entity;
    const addressParts = [
      attrs.street,
      [attrs.postal_code, attrs.city].filter(Boolean).join(" "),
    ].filter(Boolean);
    const address = addressParts.join(", ");
    // Localized server-built weekly schedule ("Mo–Fr 08:00–20:00 · Sa
    // 07:30–18:00" / "24 h geöffnet"), absent when the source has no
    // schedule data for this site.
    const openingHours = attrs.opening_hours || "";

    // When the site is closed (outside opening hours) the operator may keep
    // reporting connectors as Available — technically correct, but you can't
    // charge right now, so the tiles must not stay green (user report).
    const siteClosed = attrs.site_status === "closed";
    const closedWord =
      SITE_STATUS_WORDS.closed[this._lang()] || SITE_STATUS_WORDS.closed.en;

    // Optional per-card plug-type filter (config `plug_types`, set in the
    // visual editor): purely visual — the favorite, its sensors, and the
    // dashboard row keep covering the whole site. Availability badge counts
    // only the visible connectors then. Tiles keep their original number so
    // they still match the per-connector sensors. If the filter matches
    // nothing (e.g. the operator renamed its plug strings), fall back to
    // showing everything rather than a dead card.
    const visiblePlugs =
      Array.isArray(this._config.plug_types) && this._config.plug_types.length
        ? this._config.plug_types
        : null;
    const plugMatch = (plugs) =>
      !visiblePlugs ||
      (Array.isArray(plugs) && plugs.some((p) => visiblePlugs.includes(p)));

    let boxes;
    let badge = "";
    let badgeClass = "";
    if (isSite) {
      let shown = attrs.connectors.map((c, i) => ({ c, num: i + 1 }));
      if (visiblePlugs) {
        const filtered = shown.filter(({ c }) => plugMatch(c.plug_types));
        if (filtered.length) shown = filtered;
      }
      const filtering = shown.length !== attrs.connectors.length;
      const total = filtering
        ? shown.length
        : attrs.count_total || attrs.connectors.length;
      const available = filtering
        ? shown.filter(({ c }) => c.status_raw === "Available").length
        : Number(stateObj.state) || 0;
      const siteWords = SITE_STATUS_WORDS[attrs.site_status];
      if (siteWords) {
        // Whole site closed / out of service — show the reason instead of
        // "0/6". Full-day closures (e.g. Sundays) say "closed today" so the
        // schedule-driven closure reads differently from being outside
        // today's hours.
        badge =
          siteClosed && attrs.closed_all_day_today
            ? CLOSED_TODAY_WORDS[this._lang()] || CLOSED_TODAY_WORDS.en
            : siteWords[this._lang()] || siteWords.en;
        badgeClass = siteClosed ? "closed" : "alert";
      } else {
        // Availability badge mirrors the tile colors: green while something
        // is still free, red when every connector is taken.
        const word = AVAILABLE_WORD[this._lang()] || AVAILABLE_WORD.en;
        badge = `${available}/${total} ${word}`;
        badgeClass = available > 0 ? "ok" : "busy";
      }
      boxes = shown.map(({ c, num }) =>
        this._box(
          siteClosed ? "Closed" : c.status_raw,
          siteClosed ? closedWord : c.status,
          c.power_kw,
          shortPlugs(c.plug_types, this._lang()),
          num
        )
      );
    } else {
      boxes = [
        this._box(
          siteClosed ? "Closed" : attrs.status_raw,
          siteClosed ? closedWord : stateObj.state,
          attrs.power_kw,
          shortPlugs(attrs.plug_types, this._lang()),
          null
        ),
      ];
    }

    this._root.innerHTML = `
      <style>
        .wrap { padding: 12px 16px 16px; }
        .head {
          display: flex; align-items: baseline; justify-content: space-between;
          gap: 8px; margin-bottom: 2px;
        }
        .title {
          font-size: 1.15em; font-weight: 500;
          color: var(--primary-text-color);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .badge {
          flex: none; font-size: 0.85em; font-weight: 500;
          padding: 2px 10px; border-radius: 12px;
          background: var(--secondary-background-color, rgba(127,127,127,.15));
          color: var(--primary-text-color);
        }
        .badge.eco {
          background: #1B5E20;
          padding: 2px 8px;
          display: flex; align-items: center;
        }
        .badge.alert {
          background: var(--disabled-text-color, #757575);
          color: #fff;
        }
        .badge.closed {
          background: var(--info-color, #1565c0);
          color: #fff;
        }
        .badge.ok {
          background: var(--success-color, #2e7d32);
          color: #fff;
        }
        .badge.busy {
          background: var(--error-color, #c62828);
          color: #fff;
        }
        .addr {
          font-size: 0.85em; color: var(--secondary-text-color);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .subhead { margin-bottom: 12px; }
        .grid {
          display: grid; gap: 8px;
          grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
        }
        .box {
          border-radius: 8px; padding: 10px 8px; color: #fff;
          display: flex; flex-direction: column; align-items: center;
          gap: 2px; text-align: center; cursor: pointer;
          min-height: 64px; justify-content: center;
        }
        .box .num { font-size: 1em; font-weight: 700; opacity: 0.95; }
        .box .plug { font-size: 0.95em; font-weight: 600; }
        .box .power { font-size: 1.1em; font-weight: 600; }
        .box .status { font-size: 0.85em; font-weight: 500; }
      </style>
      <ha-card>
        <div class="wrap">
          <div class="head">
            <div class="title">${this._escape(title)}</div>
            ${renewable ? `<div class="badge eco" title="Renewable energy"><svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="#fff" d="M17,8C8,10 5.9,16.17 3.82,21.34L5.71,22L6.66,19.7C7.14,19.87 7.64,20 8,20C19,20 22,3 22,3C21,5 14,5.25 9,6.25C4,7.25 2,11.5 2,13.5C2,15.5 3.75,17.25 3.75,17.25C7,8 17,8 17,8Z"/></svg></div>` : ""}
            ${badge ? `<div class="badge${badgeClass ? ` ${badgeClass}` : ""}">${this._escape(badge)}</div>` : ""}
          </div>
          <div class="subhead">
            ${address ? `<div class="addr">${this._escape(address)}</div>` : ""}
            ${openingHours ? `<div class="addr">${this._escape(openingHours)}</div>` : ""}
          </div>
          <div class="grid">${boxes.join("")}</div>
        </div>
      </ha-card>`;

    this._root.querySelector("ha-card").addEventListener("click", () => {
      this.dispatchEvent(
        new CustomEvent("hass-more-info", {
          bubbles: true,
          composed: true,
          detail: { entityId: this._config.entity },
        })
      );
    });
  }

  _box(statusRaw, statusText, powerKw, plugShort, num) {
    const color = STATUS_COLORS[statusRaw] || UNKNOWN_COLOR;
    const power = powerKw ? `${Number(powerKw)} kW` : "";
    return `<div class="box" style="background:${color};">
      ${num !== null ? `<div class="num">#${num}</div>` : ""}
      ${plugShort ? `<div class="plug">${this._escape(plugShort)}</div>` : ""}
      ${power ? `<div class="power">${this._escape(power)}</div>` : ""}
      <div class="status">${this._escape(statusText || "?")}</div>
    </div>`;
  }

  _escape(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }
}

customElements.define("austrian-charging-stations-card", AustrianChargingStationsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "austrian-charging-stations-card",
  name: "Austrian Charging Stations Card",
  description:
    "Colored per-connector status boxes for a favorite charging station or site (ladestellen.at).",
  preview: true,
});
