/*
 * Holavonat dashboard card.
 *
 *   type: custom:holavonat-card
 *   route: Szeged ⇄ Szatymaz   # optional, device name of the route; all routes when omitted
 *   title: Ingázás             # optional
 */

const STRINGS = {
  hu: {
    now: "most indul",
    min: "perc",
    in: "",
    onTime: "pontos",
    noData: "menetrend szerint",
    hour: "ó",
    minShort: "p",
    platform: "vágány",
    at: "Most",
    arrives: "Érkezés",
    scheduledShort: "menetrend",
    actualShort: "valós",
    none: "Nincs több közvetlen vonat a következő héten.",
    noRoutes: "Nincs beállított Holavonat útvonal.",
    bus: "Pótlóbusz",
    departed: "elindult",
  },
  en: {
    now: "departing",
    min: "min",
    in: "in",
    onTime: "on time",
    noData: "scheduled",
    hour: "h",
    minShort: "m",
    platform: "platform",
    at: "Now",
    arrives: "Arrival",
    scheduledShort: "scheduled",
    actualShort: "actual",
    none: "No more direct trains in the next week.",
    noRoutes: "No Holavonat route configured.",
    bus: "Replacement bus",
    departed: "departed",
  },
};

const TRAIN_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2C8 2 4 2.5 4 6v9.5A3.5 3.5 0 0 0 7.5 19L6 20.5v.5h2.23l2-2H14l2 2h2v-.5L16.5 19a3.5 3.5 0 0 0 3.5-3.5V6c0-3.5-3.58-4-8-4M7.5 17A1.5 1.5 0 0 1 6 15.5 1.5 1.5 0 0 1 7.5 14 1.5 1.5 0 0 1 9 15.5 1.5 1.5 0 0 1 7.5 17m3.5-7H6V6h5zm2 0V6h5v4zm3.5 7a1.5 1.5 0 0 1-1.5-1.5 1.5 1.5 0 0 1 1.5-1.5 1.5 1.5 0 0 1 1.5 1.5 1.5 1.5 0 0 1-1.5 1.5"/></svg>`;
const BUS_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M18 11H6V6h12M16.5 17a1.5 1.5 0 0 1-1.5-1.5 1.5 1.5 0 0 1 1.5-1.5 1.5 1.5 0 0 1 1.5 1.5 1.5 1.5 0 0 1-1.5 1.5m-9 0A1.5 1.5 0 0 1 6 15.5 1.5 1.5 0 0 1 7.5 14 1.5 1.5 0 0 1 9 15.5 1.5 1.5 0 0 1 7.5 17M4 16c0 .88.39 1.67 1 2.22V20a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-1h8v1a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-1.78c.61-.55 1-1.34 1-2.22V6c0-3.5-3.58-4-8-4s-8 .5-8 4z"/></svg>`;

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class HolavonatCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._lastKey = null;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    // Countdowns change without new state, so re-render twice a minute.
    this._timer = setInterval(() => {
      this._lastKey = null;
      this._render();
    }, 30000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  static getStubConfig() {
    return {};
  }

  get _t() {
    const lang = (this._hass?.locale?.language || this._hass?.language || "en").slice(0, 2);
    return STRINGS[lang] || STRINGS.en;
  }

  _routes() {
    const hass = this._hass;
    const routes = new Map();
    for (const [entityId, entry] of Object.entries(hass.entities || {})) {
      if (entry.platform !== "holavonat") continue;
      const state = hass.states[entityId];
      const attrs = state?.attributes;
      if (!attrs || !attrs.direction) continue;
      const device = hass.devices?.[entry.device_id];
      const routeName = device?.name_by_user || device?.name || "";
      if (this._config.route && routeName !== this._config.route) continue;
      if (!routes.has(entry.device_id)) routes.set(entry.device_id, { name: routeName, directions: {} });
      const dirs = routes.get(entry.device_id).directions;
      if (!dirs[attrs.direction]) {
        dirs[attrs.direction] = { origin: attrs.origin, destination: attrs.destination, trains: [] };
      }
      if (state.state && !["unknown", "unavailable"].includes(state.state)) {
        dirs[attrs.direction].trains.push({ position: attrs.position, state: state.state, ...attrs });
      }
    }
    for (const route of routes.values()) {
      for (const dir of Object.values(route.directions)) dir.trains.sort((a, b) => a.position - b.position);
    }
    return [...routes.values()].sort((a, b) => a.name.localeCompare(b.name));
  }

  _time(iso) {
    if (!iso) return "";
    return new Date(iso).toLocaleTimeString(this._hass?.locale?.language || "hu", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  _countdown(iso) {
    const t = this._t;
    const minutes = Math.round((new Date(iso) - Date.now()) / 60000);
    if (minutes <= 0) return `<span class="soon">${t.now}</span>`;
    if (minutes < 60) {
      return `<span class="${minutes <= 10 ? "soon" : ""}">${t.in ? t.in + " " : ""}<b>${minutes}</b> ${t.min}</span>`;
    }
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return `<span>${t.in ? t.in + " " : ""}<b>${h}</b> ${t.hour} <b>${m}</b> ${t.minShort}</span>`;
  }

  _delayBadge(train) {
    const t = this._t;
    if (!train.realtime) return `<span class="badge muted">${t.noData}</span>`;
    const d = train.delay_min ?? 0;
    if (d <= 0) return `<span class="badge ok">${t.onTime}</span>`;
    return `<span class="badge ${d >= 10 ? "bad" : "warn"}">+${d} ${t.min}</span>`;
  }

  _trainRow(train, first) {
    const t = this._t;
    const delayed = train.realtime && (train.delay_min ?? 0) > 0;
    const meta = [
      `<span class="chip">${train.replacement_bus ? BUS_ICON : TRAIN_ICON}${esc(train.train || "")}</span>`,
      train.platform ? `<span class="chip">${esc(train.platform)}. ${t.platform}</span>` : "",
      train.replacement_bus ? `<span class="chip bus">${t.bus}</span>` : "",
    ].join("");
    const where = train.realtime && train.current_stop
      ? `<div class="where">${t.at}: ${esc(train.current_stop)}</div>`
      : "";
    return `
      <div class="train ${first ? "first" : ""}">
        <div class="when">
          <div class="time ${delayed ? "late" : ""}">${this._time(train.state)}</div>
          ${delayed ? `<div class="sched">${this._time(train.scheduled_departure)}</div>` : ""}
        </div>
        <div class="info">
          <div class="meta">${meta}</div>
          ${this._arrival(train)}
          ${where}
        </div>
        <div class="right">
          <div class="countdown">${this._countdown(train.state)}</div>
          ${this._delayBadge(train)}
        </div>
      </div>`;
  }

  _arrival(train) {
    const t = this._t;
    const scheduled = this._time(train.scheduled_arrival);
    let actual = "";
    if (train.realtime && train.arrival_delay_min != null) {
      const d = train.arrival_delay_min;
      const cls = d >= 10 ? "bad" : d > 0 ? "late" : "ok";
      actual = ` · ${t.actualShort} <b class="arr ${cls}">${this._time(train.expected_arrival)}</b>`;
    }
    return `<div class="sub arrival">${t.arrives}: ${t.scheduledShort} <b>${scheduled}</b>${actual}</div>`;
  }

  _direction(dir) {
    const t = this._t;
    const rows = dir.trains.length
      ? dir.trains.map((train, i) => this._trainRow(train, i === 0)).join("")
      : `<div class="empty">${t.none}</div>`;
    return `
      <section class="direction">
        <header>
          <span class="from">${esc(dir.origin)}</span>
          <span class="arrow">→</span>
          <span class="to">${esc(dir.destination)}</span>
        </header>
        ${rows}
      </section>`;
  }

  _render() {
    if (!this._hass || !this.shadowRoot) return;
    const routes = this._routes();
    const key = JSON.stringify(routes) + (this._config.title || "");
    if (key === this._lastKey) return;
    this._lastKey = key;

    const body = routes.length
      ? routes
          .map(
            (route) => `
          <div class="route">
            ${routes.length > 1 || !this._config.title ? `<div class="route-name">${esc(route.name)}</div>` : ""}
            <div class="directions">
              ${["outbound", "return"].filter((d) => route.directions[d]).map((d) => this._direction(route.directions[d])).join("")}
            </div>
          </div>`,
          )
          .join("")
      : `<div class="empty">${this._t.noRoutes}</div>`;

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        ${this._config.title ? `<div class="title">${esc(this._config.title)}</div>` : ""}
        ${body}
        <div class="footer">MÁV GTFS · holavonat.is</div>
      </ha-card>`;
  }
}

const STYLE = `
  :host {
    --hv-accent: var(--primary-color, #03a9f4);
    --hv-ok: var(--success-color, #43a047);
    --hv-warn: var(--warning-color, #ffa000);
    --hv-bad: var(--error-color, #e53935);
    --hv-muted: var(--secondary-text-color, #727272);
    --hv-line: var(--divider-color, rgba(127,127,127,.2));
  }
  ha-card { padding: 16px; overflow: hidden; }
  .title { font-size: 1.25rem; font-weight: 600; margin: 0 0 12px; }
  .route + .route { margin-top: 20px; }
  .route-name { font-size: .8rem; letter-spacing: .06em; text-transform: uppercase; color: var(--hv-muted); margin-bottom: 8px; }
  .directions { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
  .direction {
    border-radius: 14px;
    background: color-mix(in srgb, var(--hv-accent) 6%, transparent);
    border: 1px solid color-mix(in srgb, var(--hv-accent) 18%, transparent);
    padding: 10px 12px 4px;
  }
  header { display: flex; align-items: baseline; gap: 6px; font-weight: 600; font-size: 1rem; margin-bottom: 4px; flex-wrap: wrap; }
  header .arrow { color: var(--hv-accent); }
  .train { display: grid; grid-template-columns: 76px 1fr auto; gap: 12px; align-items: center; padding: 10px 0; }
  .train + .train { border-top: 1px solid var(--hv-line); }
  .time { font-size: 1.35rem; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.1; }
  .train.first .time { font-size: 1.7rem; }
  .time.late { color: var(--hv-warn); }
  .sched { font-size: .8rem; color: var(--hv-muted); text-decoration: line-through; font-variant-numeric: tabular-nums; }
  .info { min-width: 0; }
  .meta { display: flex; flex-wrap: wrap; gap: 4px; }
  .chip {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: .78rem; font-weight: 500; padding: 2px 8px; border-radius: 999px;
    background: color-mix(in srgb, var(--primary-text-color, #000) 7%, transparent);
    white-space: nowrap;
  }
  .chip svg { width: 14px; height: 14px; color: var(--hv-accent); }
  .chip.bus { background: color-mix(in srgb, var(--hv-warn) 20%, transparent); }
  .sub, .where { font-size: .8rem; color: var(--hv-muted); margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sub.arrival { white-space: normal; }
  .sub b { font-weight: 600; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .sub b.arr.late { color: var(--hv-warn); }
  .sub b.arr.bad { color: var(--hv-bad); }
  .sub b.arr.ok { color: var(--hv-ok); }
  .right { text-align: right; display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
  .countdown { font-size: .85rem; color: var(--hv-muted); white-space: nowrap; }
  .countdown b { font-size: 1.05rem; color: var(--primary-text-color); }
  .countdown .soon, .countdown .soon b { color: var(--hv-accent); font-weight: 600; }
  .badge { font-size: .72rem; font-weight: 600; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
  .badge.ok { color: var(--hv-ok); background: color-mix(in srgb, var(--hv-ok) 15%, transparent); }
  .badge.warn { color: var(--hv-warn); background: color-mix(in srgb, var(--hv-warn) 15%, transparent); }
  .badge.bad { color: var(--hv-bad); background: color-mix(in srgb, var(--hv-bad) 15%, transparent); }
  .badge.muted { color: var(--hv-muted); background: color-mix(in srgb, var(--hv-muted) 12%, transparent); font-weight: 500; }
  .empty { color: var(--hv-muted); font-size: .9rem; padding: 12px 0; }
  .footer { margin-top: 10px; font-size: .7rem; color: var(--hv-muted); text-align: right; }
`;

customElements.define("holavonat-card", HolavonatCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "holavonat-card",
  name: "Holavonat",
  description: "A következő vonatok mindkét irányban, késéssel.",
  preview: true,
});
