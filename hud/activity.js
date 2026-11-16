/* Who did what: actor chips, verbs and rows shared by the audit views. */
import { api, h, icon } from "./lib.js";

const ACTOR_ICON = { device: "devices", mac: "terminals", agent: "workers" };

const kindOf = (actor) => String((actor && actor.kind) || "").toLowerCase();

export function actorChip(actor) {
  const a = actor || {};
  const name = a.name || a.id || "Unknown";
  return h("span", { class: "actor-chip", title: a.id || "" },
    icon(ACTOR_ICON[kindOf(a)] || "workers"),
    h("span", { class: "actor-chip-name" }, name));
}

export function fmtDate(ts) {
  const n = Number(ts);
  if (!Number.isFinite(n) || n <= 0) return "";
  return new Date(n * 1000).toLocaleString(undefined, {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

const cleanAction = (entry) => String((entry && entry.action) || "").replace(/^\/api\//, "");

export function verb(entry) {
  const action = cleanAction(entry);
  const target = (entry && entry.target) || {};
  switch (action) {
    case "stop_worker": return "stopped";
    case "approve_worker": return "approved";
    case "reject_worker": return "rejected";
    case "answer_permission": return target.decision ? `answered a permission (${target.decision})` : "answered a permission";
    case "spin_worker":
    case "spawn": return "started a worker";
    case "instruct_worker": return "instructed";
    case "send_note":
    case "mail": return "sent a note";
    case "pair/approve": return "approved a device";
    case "pair/revoke": return "revoked a device";
    default: return action || "acted";
  }
}

const targetOf = (entry) => {
  const t = (entry && entry.target) || {};
  return t.worker_id || t.device_id || t.filepath || "";
};

const sameActor = (a, b) => {
  const x = a || {};
  const y = b || {};
  return String(x.kind || "") === String(y.kind || "") && String(x.id || "") === String(y.id || "");
};

export function activityRow(entry) {
  const e = entry || {};
  const origin = e.origin || {};
  const target = targetOf(e);
  return h("div", { class: "audit-row" },
    h("span", { class: "audit-time" }, fmtDate(e.ts)),
    actorChip(e.actor),
    !sameActor(e.origin, e.actor) && origin.name ? h("span", { class: "audit-origin" }, `from ${origin.name}`) : null,
    h("span", { class: "audit-verb" }, verb(e)),
    target ? h("span", { class: "audit-target mono" }, target) : null);
}

export function stampLine(entry, fallbackTs) {
  if (!entry) {
    const date = fmtDate(fallbackTs);
    return date ? h("div", { class: "stamp-line" }, date) : null;
  }
  const origin = entry.origin || {};
  return h("div", { class: "stamp-line" },
    h("span", {}, "Started by ", actorChip(entry.actor)),
    origin.name ? h("span", {}, ` · from ${origin.name}`) : null,
    h("span", {}, ` · ${fmtDate(entry.ts || fallbackTs)}`));
}

/* Started-by stamps, fetched once for all visible workers and cached by id. */
const startedCache = new Map();

export function startedEntry(id) {
  return startedCache.get(id) || null;
}

export function ensureStartedStamps(ids) {
  const unique = [...new Set((ids || []).filter(Boolean))];
  const missing = unique.filter((id) => !startedCache.has(id));
  if (!missing.length) return;
  /* Reserve now so overlapping renders cannot request the same ids again. */
  for (const id of missing) startedCache.set(id, null);
  api(`/api/audit/started?ids=${encodeURIComponent(missing.join(","))}`)
    .then((d) => {
      const started = (d && d.started) || {};
      for (const id of missing) startedCache.set(id, started[id] || null);
      document.dispatchEvent(new Event("subway:rerender"));
    })
    .catch(() => { /* Leave the cached miss; the card shows its own date instead. */ });
}

/* The ledger's Activity view and the live audit stream. */
const activity = { entries: [], loading: null };
const AUDIT_LIMIT = 100;

export function recentActivity() {
  return activity.entries;
}

function mergeEntries(a, b) {
  const bySeq = new Map();
  for (const e of [...(a || []), ...(b || [])]) {
    if (!e) continue;
    const key = e.seq != null ? `s${e.seq}` : `${e.ts}|${e.action}|${(e.actor || {}).id || ""}`;
    if (!bySeq.has(key)) bySeq.set(key, e);
  }
  return [...bySeq.values()].sort((x, y) => Number(y.seq || y.ts || 0) - Number(x.seq || x.ts || 0)).slice(0, AUDIT_LIMIT);
}

export function loadActivity(limit = AUDIT_LIMIT) {
  if (activity.loading) return activity.loading;
  activity.loading = api(`/api/audit?limit=${encodeURIComponent(limit)}`)
    .then((d) => {
      activity.entries = mergeEntries(d && d.entries, activity.entries);
      document.dispatchEvent(new Event("subway:activity"));
    })
    .catch(() => { /* keep the last list */ })
    .finally(() => { activity.loading = null; });
  return activity.loading;
}

export function prependActivity(entry) {
  if (!entry) return;
  activity.entries = mergeEntries([entry], activity.entries);
  const w = (entry.target || {}).worker_id;
  if (w && ["spin_worker", "spawn"].includes(cleanAction(entry))) startedCache.set(w, entry);
  document.dispatchEvent(new Event("subway:activity"));
}

let streamStarted = false;

export function startAuditStream() {
  if (streamStarted || typeof EventSource === "undefined") return;
  streamStarted = true;
  const token = document.querySelector('meta[name="subway-token"]');
  const es = new EventSource(`/api/events?token=${encodeURIComponent(token ? token.content : "")}`);
  es.addEventListener("audit", (ev) => {
    try { prependActivity(JSON.parse(ev.data)); } catch (e) { /* ignore malformed frames */ }
  });
  es.addEventListener("worker_media", (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data && data.id) import("./views.js").then((m) => m.forgetWorkerMedia(data.id)).catch(() => {});
      document.dispatchEvent(new Event("subway:rerender"));
    } catch (e) { /* ignore malformed frames */ }
  });
}
