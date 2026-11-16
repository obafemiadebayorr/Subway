/* Devices page: this station, paired devices, pending pair requests, and pairing a new device. */
import { act, api, field, h, icon, modal } from "./lib.js";
import { startPairingCard } from "./pairing.js";
import { linkedStationsSection } from "./links.js";
import qrcode from "./qrcode.js";
import { fmtDate, verb } from "./activity.js";

const state = { data: undefined, station: undefined, telemetry: undefined, actions: new Map() };

async function load(refresh = false) {
  if (state.data !== undefined && !refresh) return;
  if (!refresh) state.data = null; // loading sentinel, so devicesPage() does not fire the fetch twice
  const [pair, station, telemetry] = await Promise.allSettled([
    api("/api/pair/list"),
    api("/api/station"),
    api("/api/station/telemetry"),
  ]);
  state.data = pair.status === "fulfilled" ? (pair.value || {}) : { error: pair.reason?.message || "Could not load devices" };
  state.station = station.status === "fulfilled" ? (station.value || {}) : { error: station.reason?.message || "Could not load this station" };
  state.telemetry = telemetry.status === "fulfilled" ? (telemetry.value?.telemetry ?? null) : { error: telemetry.reason?.message || "Could not load telemetry" };
  const devices = (Array.isArray(state.data?.devices) ? state.data.devices : []).filter((d) => d && !d.revoked && d.device_id);
  const audits = await Promise.allSettled(devices.map((d) => api(`/api/audit?actor=${encodeURIComponent(d.device_id)}&limit=1`)));
  state.actions = new Map();
  audits.forEach((r, i) => {
    if (r.status === "fulfilled") state.actions.set(devices[i].device_id, ((r.value && r.value.entries) || [])[0] || null);
  });
  document.dispatchEvent(new Event("subway:rerender"));
}

function relTime(epoch) {
  const t = Number(epoch);
  if (!Number.isFinite(t) || t <= 0) return "never";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
  if (s < 10) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const hrs = Math.floor(m / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const d = Math.floor(hrs / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(t * 1000).toLocaleDateString();
}

const deviceKind = (d) => d.kind || d.platform || "device";
const deviceScopes = (d) => (Array.isArray(d.scopes) ? d.scopes : [d.scope || "device"]).filter(Boolean);
const isOnline = (d) => Number(d.last_seen) > 0 && Date.now() / 1000 - Number(d.last_seen) < 300;

function invalidate() {
  state.data = undefined;
  state.station = undefined;
  state.telemetry = undefined;
  state.actions = new Map();
  document.dispatchEvent(new Event("subway:rerender"));
}

function renameModal(device) {
  const input = h("input", { type: "text", value: device.name || "", required: true, autocomplete: "off", maxlength: 80 });
  const form = h("form", { class: "form", on: { submit: async (e) => {
    e.preventDefault();
    const name = input.value.trim();
    if (!name) return;
    const res = await act("/api/pair/rename", { device_id: device.device_id, name });
    if (res) { invalidate(); dlg.close(); }
  } } },
    field("Name", input, true),
    h("div", { class: "btns field full" }, h("button", { class: "btn primary", type: "submit" }, "Rename")));
  const dlg = modal(`Rename ${device.name || "device"}`, form);
  input.focus();
  input.select();
}

function revokeModal(device) {
  const name = device.name || device.device_id || "this device";
  const dlg = modal("Revoke device", h("div", { class: "rows" },
    h("p", { class: "row-sub" }, `Revoke \u201c${name}\u201d? It will immediately lose access to this station and must pair again.`),
    h("div", { class: "btns" },
      h("button", { class: "btn danger", type: "button", on: { click: async () => {
        const res = await act("/api/pair/revoke", { device_id: device.device_id });
        if (res) { invalidate(); dlg.close(); }
      } } }, "Revoke"),
      h("button", { class: "btn", type: "button", on: { click: () => dlg.close() } }, "Cancel"))));
}

function stationCard() {
  const s = state.station;
  if (s === undefined) return h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { class: "card-title" }, "This station")), h("div", { class: "empty" }, "Loading…"));
  if (s.error) return h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { class: "card-title" }, "This station")), h("div", { class: "empty" }, s.error));
  const dev = s.device && typeof s.device === "object" ? s.device : {};
  const routes = Array.isArray(s.routes) ? s.routes : [];
  const providers = Array.isArray(s.providers) ? s.providers : [];
  const models = Array.isArray(s.models) ? s.models : [];
  const localModels = Array.isArray(s.local_models) ? s.local_models : [];
  const cap = s.worker_capacity || s.capacity || {};
  const quotaLeft = s.quota_left && typeof s.quota_left === "object" ? s.quota_left : {};
  const host = [dev.hostname, [dev.os, dev.arch].filter(Boolean).join(" ")].filter(Boolean).join(" · ") || "unknown host";
  const ram = (dev.total_ram_gb || dev.free_ram_gb) ? `RAM ${dev.free_ram_gb ?? "?"} / ${dev.total_ram_gb ?? "?"} GB free` : "";
  const localSummary = localModels.length
    ? localModels.map((m) => `${m.model || m.name || m.id || "model"}${m.memory_fit === true ? " (fits)" : m.memory_fit === false ? " (no fit)" : ""}`).slice(0, 6).join(", ")
    : "none detected";
  const quotaSummary = Object.keys(quotaLeft).length
    ? Object.entries(quotaLeft).map(([k, v]) => `${k}: ${v == null ? "—" : v}`).join(" · ")
    : "none reported";
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", { class: "card-title" }, "This station"),
      h("div", { class: "card-sub" }, "Person, device, providers/models, local models (memory fit), worker capacity, quota left, default initiator.")),
      s.version ? h("span", { class: "tag" }, `v${s.version}`) : null),
    h("div", { class: "rows" },
      h("div", { class: "row device-row" }, h("div", {}, h("div", { class: "row-title" }, s.station_id || "station"), h("div", { class: "row-sub" }, [s.person || "user", host, ram].filter(Boolean).join(" · ")))),
      h("div", { class: "row device-row" }, h("div", {}, h("div", { class: "row-title" }, "Default initiator"), h("div", { class: "row-sub" }, String(s.default_initiator || "principal"))),
        h("div", { class: "tags" }, routes.map((r) => h("span", { class: "tag accent" }, String(r))))),
      h("div", { class: "row device-row" }, h("div", {}, h("div", { class: "row-title" }, "Providers & models"), h("div", { class: "row-sub" }, providers.length ? providers.join(", ") : "none detected")),
        h("span", { class: "tag" }, `${models.length || s.models_count || 0} models`)),
      h("div", { class: "row device-row" }, h("div", {}, h("div", { class: "row-title" }, "Local models (memory fit)"), h("div", { class: "row-sub" }, localSummary))),
      h("div", { class: "row device-row" }, h("div", {}, h("div", { class: "row-title" }, "Worker capacity"), h("div", { class: "row-sub" }, Object.keys(cap || {}).length ? JSON.stringify(cap) : "unavailable")),
        h("div", {}, h("div", { class: "row-title" }, "Quota left"), h("div", { class: "row-sub" }, quotaSummary)))),
    telemetryTiles());
}

function telemetryTiles() {
  const t = state.telemetry;
  if (t === undefined) return h("div", { class: "card-sub" }, "Loading telemetry…");
  if (t && t.error) return h("div", { class: "card-sub" }, t.error);
  if (!t) return h("div", { class: "card-sub" }, "No telemetry reported yet — a paired phone reports its battery and network state.");
  const entries = Object.entries(t).filter(([, v]) => v !== undefined);
  if (!entries.length) return h("div", { class: "card-sub" }, "No telemetry reported yet.");
  return h("div", { class: "devices-tiles" }, entries.map(([key, value]) => {
    let v = value;
    if (key === "updated_at" && Number.isFinite(Number(v))) v = relTime(Number(v));
    else if (typeof v === "boolean") v = v ? "yes" : "no";
    else if (key === "battery_pct" && v !== null) v = `${v}%`;
    else if (v === null) v = "—";
    else if (typeof v === "object") v = JSON.stringify(v);
    const label = key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
    return h("div", { class: "device-tile" }, h("div", { class: "device-tile-label" }, label), h("div", { class: "device-tile-value" }, String(v)));
  }));
}

function deviceCard(d) {
  const online = isOnline(d);
  const name = d.name || "Unnamed device";
  const last = state.actions.get(d.device_id) || null;
  const lastTarget = last ? (((last.target || {}).worker_id) || ((last.target || {}).device_id) || ((last.target || {}).filepath) || "") : "";
  return h("div", { class: "device-card" },
    h("div", { class: "device-card-head" },
      h("span", { class: `dot ${online ? "ok" : "bad"}`, title: online ? "Online" : "Offline", role: "img", "aria-label": online ? "Online" : "Offline" }),
      h("div", { class: "device-card-name" }, name),
      h("span", { class: "tag" }, deviceKind(d))),
    h("div", { class: "row-sub" }, `Last seen ${relTime(d.last_seen)}`),
    last ? h("div", { class: "device-last-action" },
      "Last action: ", h("b", {}, verb(last)), lastTarget ? ` ${lastTarget}` : "", ` · ${fmtDate(last.ts)}`) : null,
    h("div", { class: "tags" }, deviceScopes(d).map((s) => h("span", { class: "tag accent" }, String(s)))),
    h("div", { class: "btns" },
      h("button", { class: "btn", type: "button", on: { click: () => renameModal(d) } }, "Rename"),
      h("button", { class: "btn danger", type: "button", on: { click: () => revokeModal(d) } }, "Revoke")));
}

function devicesSection(pair) {
  const devices = (Array.isArray(pair?.devices) ? pair.devices : []).filter((d) => d && !d.revoked);
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", { class: "card-title" }, "Paired devices"),
      h("div", { class: "card-sub" }, "Phones and Macs that can reach this station with their own token."))),
    devices.length
      ? h("div", { class: "devices-grid" }, devices.map(deviceCard))
      : h("div", { class: "empty" }, "No devices paired yet"));
}

function pendingSection(pair) {
  const requests = (Array.isArray(pair?.requests) ? pair.requests : []).filter((r) => r && (r.status || "pending") === "pending");
  if (!requests.length) return null;
  const answered = () => { invalidate(); };
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", { class: "card-title" }, "Pending requests"),
      h("div", { class: "card-sub" }, "Approve only if this is your device. Requests expire after 10 minutes."))),
    h("div", { class: "rows" }, requests.map((r) => h("div", { class: "row" },
      h("div", {},
        h("div", { class: "row-title" }, r.device || "Unknown device"),
        h("div", { class: "row-sub" }, [r.platform || "unknown", r.ip || "", r.age_s != null ? `${Math.round(Number(r.age_s))}s ago` : ""].filter(Boolean).join(" · ")),
        h("div", { class: "pair-code-inline" }, r.code || "------")),
      h("div", { class: "btns" },
        h("button", { class: "btn primary", type: "button", on: { click: async () => { if (await act("/api/pair/approve", { request_id: r.request_id })) answered(); } } }, "Approve"),
        h("button", { class: "btn danger", type: "button", on: { click: async () => { if (await act("/api/pair/deny", { request_id: r.request_id })) answered(); } } }, "Deny"))))));
}

/* QR pairing over Subway Tunnel: a one-time code (5 min) the phone scans from any network.
   The box lives outside the render cycle so a re-render keeps the code and its countdown. */
const qr = { box: null, timer: 0 };

function qrPanel() {
  if (!qr.box) qr.box = h("div", { class: "qr-pair", style: "display:none;flex-direction:column;align-items:center;gap:8px;padding:12px 0" });
  return qr.box;
}

async function showQr() {
  const box = qrPanel();
  box.style.display = "flex";
  clearInterval(qr.timer);
  box.replaceChildren(h("div", { class: "row-sub" }, "Making a pairing code…"));
  let res;
  try {
    res = await api("/api/pair/qr", {});
  } catch (e) {
    const msg = /not set up/.test(e.message) ? "Start the tunnel first: subway tunnel --url wss://…" : e.message;
    box.replaceChildren(h("div", { class: "row-sub" }, msg));
    return;
  }
  const code = qrcode(0, "M");
  code.addData(res.link);
  code.make();
  // Dark modules on white in both themes: scanners need the contrast.
  const art = h("div", { role: "img", "aria-label": "Pairing QR code", style: "background:#fff;padding:12px;border-radius:12px;width:min(240px,70vw);line-height:0" });
  art.innerHTML = code.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  const left = h("div", { class: "row-sub", "aria-live": "polite" });
  box.replaceChildren(art, left,
    h("button", { class: "btn", type: "button", on: { click: showQr } }, "New code"),
    h("div", { class: "row-sub" }, "In the Subway app: Connect → Scan QR from your Mac. Works from any network."));
  const tick = () => {
    const sec = Math.max(0, Math.round(res.expires_at - Date.now() / 1000));
    if (sec === 0) {
      clearInterval(qr.timer);
      art.style.opacity = "0.15";
      left.textContent = "Expired";
      return;
    }
    left.textContent = `Expires in ${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  };
  tick();
  qr.timer = setInterval(tick, 1000);
}

function pairNewSection() {
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", { class: "card-title" }, "Pair a new device"),
      h("div", { class: "card-sub" }, "On your phone or Mac, open Subway and choose to pair this station. It shows a 6-character code; requests expire after 10 minutes."))),
    h("div", { class: "rows" },
      h("div", { class: "row device-row" },
        h("div", {}, h("div", { class: "row-title" }, "Approve on this Mac"),
          h("div", { class: "row-sub" }, "The floating pairing card shows pending requests everywhere in the HUD.")),
        h("div", { class: "btns" }, h("button", { class: "btn", type: "button", on: { click: () => startPairingCard() } }, icon("plus"), h("span", {}, "Show pairing prompt")))),
      h("div", { class: "row device-row" },
        h("div", {}, h("div", { class: "row-title" }, "Pair with a QR code"),
          h("div", { class: "row-sub" }, "The phone scans it and connects through Subway Tunnel, end-to-end encrypted, with no approval dialog.")),
        h("div", { class: "btns" }, h("button", { class: "btn primary", type: "button", on: { click: showQr } }, h("span", {}, "Pair with QR")))),
      qrPanel()));
}

/* Keep the page current while it is open: a phone that pairs by QR, or goes on/offline, shows up
   within 10s without a reload. Refreshes keep the old data on screen (no "Loading…" flash). */
let refreshTimer = 0;
let onPush = null;
function keepFresh() {
  if (refreshTimer) return;
  refreshTimer = setInterval(() => {
    if (!location.hash.startsWith("#devices")) { clearInterval(refreshTimer); refreshTimer = 0; if (onPush) { window.removeEventListener("subway:push", onPush); onPush = null; } return; }
    if (!document.hidden && state.data) load(true);
  }, 30000);
  onPush = () => { if (!location.hash.startsWith("#devices")) { clearInterval(refreshTimer); refreshTimer = 0; window.removeEventListener("subway:push", onPush); onPush = null; return; } if (!document.hidden && state.data) load(true); };
  window.addEventListener("subway:push", onPush);
}

export function devicesPage(d) {
  load();
  keepFresh();
  if (state.data === undefined || state.data === null) {
    return h("div", { class: "devices-stack" }, stationCard(), h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { class: "card-title" }, "Devices")), h("div", { class: "empty" }, "Loading…")));
  }
  const pair = state.data;
  return h("div", { class: "devices-stack" },
    pair.error ? h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", { class: "card-title" }, "Devices")), h("div", { class: "empty" }, pair.error)) : null,
    stationCard(),
    devicesSection(pair),
    linkedStationsSection(),
    pendingSection(pair),
    pairNewSection());
}
