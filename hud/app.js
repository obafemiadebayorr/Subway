/* Shell, routing and polling. */
import { api, duration, get, h, icon, listen, set, subscribe, tickAll, toast, tokens, trainMark } from "./lib.js";
import { mountNotifications, notify } from "./notifications.js";
import { mountSummon } from "./summon.js";
import { mountTethers, unmountTethers } from "./tethers.js";
import { mountTetherSheet, openTetherSheet } from "./tether-sheet.js";
import { startPairingCard } from "./pairing.js";
import { themeSwitch } from "./switch-button.js";
import { hasModelEdits, ledger, ledgerPage, leasesPage, modelsPage, openLogs, overview, renderLedgerEntries, terminalsPage, terminalsState, terrainState, threads, threadsPage, usagePage, workersPage } from "./views.js";
import { statsPage } from "./stats.js";
import { diagnosticsPage } from "./diagnostics.js";
import { helpPage } from "./help.js";
import { pageSwap } from "./motion.js";

const POLL_MS = 1000;
const FEED_MS = 3000;
const PAGES = {
  overview: { label: "Overview", sub: "What needs you, and what's running" },
  threads: { label: "Threads", sub: "Chats that used Subway, and everything they started" },
  terminals: { label: "Terminals", sub: "Commands run in terminals your user has shared" },
  usage: { label: "Usage", sub: "Limits and tokens, as reported by each tool" },
  stats: { label: "Stats", sub: "Tokens, time, speed and work across every worker" },
  diagnostics: { label: "Diagnostics", sub: "System health and collector diagnostics" },
  workers: { label: "Workers", sub: "Delegated agents and their permissions" },
  models: { label: "Models", sub: "How each model is ranked, and where that came from" },
  leases: { label: "Leases", sub: "Who holds which file" },
  ledger: { label: "Ledger", sub: "Shared decisions across agents" },
  help: { label: "Help", sub: "Guides, terms, and commands for Subway" },
};

const nav = {};
const content = h("main", { class: "main", id: "content" });
const heading = h("h1", { class: "page-title" });
const subheading = h("div", { class: "page-sub" });
const linkDot = h("span", { class: "dot" });
const linkText = h("span", { class: "num" }, "Connecting");
const alertBar = h("div", { class: "alert-bar", role: "alert", hidden: true }, "The Subway server isn't responding, so what's shown may be out of date.");
const pageBody = h("div", { style: "display:flex;flex-direction:column;gap:inherit" });

function currentPage() {
  const name = location.hash.slice(1);
  return PAGES[name] ? name : "overview";
}

function buildShell() {
  const railToggle = h("button", { class: "rail-toggle", type: "button", "aria-label": "Hide sidebar", title: "Hide sidebar", on: { click: toggleRail } }, icon("sidebar"));
  const railFloat = h("button", { class: "rail-float", type: "button", "aria-label": "Show sidebar", title: "Show sidebar", on: { click: toggleRail } }, icon("sidebar"));
  const rail = h("nav", { class: "rail", "aria-label": "Sections" }, railToggle, h("div", { class: "rail-brand", "aria-hidden": "true" }, trainMark()),
    Object.entries(PAGES).filter(([key]) => key !== "help").map(([key, page]) => {
      const badge = h("span", { class: "nav-badge", hidden: true });
      const item = h("button", { class: "nav-item", type: "button", on: { click: () => { location.hash = key; } } }, icon(key), h("span", { class: "nav-label" }, page.label), badge);
      nav[key] = { item, badge };
      return item;
    }));
  const helpBtn = h("button", { class: "icon-btn", type: "button", "aria-label": "Help", on: { click: () => { location.hash = "help"; } } }, "?");
  const theme = themeSwitch({
    size: "sm",
    showLabel: true,
    getTheme: () => {
      let stored = null;
      try { stored = localStorage.getItem("subway-theme"); } catch (e) { /* private windows */ }
      if (stored === "dark" || stored === "light") return stored;
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    },
    onThemeChange: (next) => {
      try { localStorage.setItem("subway-theme", next); } catch (e) { /* private windows */ }
      applyTheme(next);
    },
  });
  theme.setAttribute("aria-label", "Toggle dark mode");
  content.append(
    h("header", { class: "topbar" },
      h("div", {}, h("div", { class: "title-mark" }, h("b", {}, "Subway"), " Context"), heading, subheading),
      h("div", { class: "top-actions" }, h("span", { class: "pill", role: "status" }, linkDot, linkText), helpBtn, theme)),
    alertBar, pageBody);
  document.addEventListener("tt:pod-open", (e) => {
    const { providerId, name, anchor } = e.detail || {};
    openTetherSheet({ providerId, name, anchor });
  });
  document.addEventListener("tt:principal", (e) => {
    const { providerId } = e.detail || {};
    const thread = document.querySelector(".tt-composer-field")?.dataset.thread || "";
    api("/api/composer/principal", { thread, principal_id: providerId }).catch((err) => {
      if (thread) toast(err?.message || "Failed to set principal");
    });
  });
  document.addEventListener("tt:mail-drop", (e) => {
    const { providerId, payload } = e.detail || {};
    const thread = document.querySelector(".tt-composer-field")?.dataset.thread || "";
    const title = (payload && payload.title) ? String(payload.title) : "chat";
    const body = {
      thread: thread || undefined,
      clock: { kind: "quota" },
      steps: [{ prompt: "Mail: review " + title + " with " + (providerId || "provider"), provider: providerId, effort: "low" }]
    };
    api("/api/composer/mail", body).catch((err) => { if (thread) toast(err?.message || "Mail failed"); });
  });
  document.getElementById("root").append(h("div", { class: "shell" }, rail, railFloat, content));
  startPairingCard();
  mountNotifications(document.body);
  mountSummon(document.body);
  mountTetherSheet(document.body);
}

const THEMES = ["auto", "dark", "light"];
function storedTheme() {
  try { return localStorage.getItem("subway-theme") || "auto"; } catch (e) { return "auto"; }
}
function applyTheme(mode) {
  if (mode === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", mode);
}
function cycleTheme() {
  const next = THEMES[(THEMES.indexOf(storedTheme()) + 1) % THEMES.length];
  try { localStorage.setItem("subway-theme", next); } catch (e) { /* Private windows can refuse storage. */ }
  applyTheme(next);
}

/* The rail folds to icons only; the choice is remembered. */
function storedRail() {
  try { return localStorage.getItem("subway-rail") || "expanded"; } catch (e) { return "expanded"; }
}
function applyRail(mode) {
  if (mode === "collapsed") document.documentElement.setAttribute("data-rail", "collapsed");
  else document.documentElement.removeAttribute("data-rail");
}
function toggleRail() {
  const next = storedRail() === "collapsed" ? "expanded" : "collapsed";
  try { localStorage.setItem("subway-rail", next); } catch (e) { /* Private windows can refuse storage. */ }
  applyRail(next);
}

function focusInside() {
  const el = document.activeElement;
  return !!el && pageBody.contains(el) && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
}

let lastAnimatedPage = null;

function render() {
  const d = get("status");
  const page = currentPage();
  for (const [key, val] of Object.entries(nav)) {
    if (!val) continue;
    const { item } = val;
    if (key === page) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  }
  heading.textContent = PAGES[page].label;
  subheading.textContent = PAGES[page].sub;
  if (!d && page !== "help") return;
  if (page === "threads") loadThreads();
  if (page === "terminals") loadTerminals();
  if (page === "overview") loadTerrain();
  if (focusInside()) return; // Never wipe out what someone is typing.
  const open = [...pageBody.querySelectorAll("details[open] summary")].map((x) => x.closest(".card")?.querySelector(".provider-name")?.textContent);
  const views = {
    overview: () => overview(d), usage: () => usagePage(d),
    stats: () => statsPage(d), diagnostics: () => diagnosticsPage(d), workers: () => workersPage(d),
    threads: () => threadsPage(d, threads.chats, () => loadThreads(true), threads.error),
    terminals: () => terminalsPage(d, terminalsState.rows, () => loadTerminals(true), terminalsState.error, terminalsState.loading, terminalsState.loadedAt > 0 && !terminalsState.error && (Date.now() - terminalsState.loadedAt > 20000), terminalsState.sharing),
    models: () => modelsPage(d),
    leases: () => leasesPage(d), ledger: () => ledgerPage(d, switchTrack),
    help: () => helpPage(),
  };
  const pageTop = window.scrollY;
  let out;
  try {
    out = views[page] ? views[page]() : null;
  } catch (err) {
    console.error("Render failed on " + page, err);
    out = h("div", { class: "card", style: "padding: 24px; border: 1px solid var(--border-bad, #e05252);" },
      h("b", { style: "color: var(--text-bad, #e05252);" }, `Rendering error on ${page}`),
      h("pre", { class: "mono", style: "margin-top: 8px; white-space: pre-wrap;" }, String(err && err.stack ? err.stack : err)));
  }
  pageBody.replaceChildren(...[out].flat(Infinity).filter(Boolean));
  if (page !== lastAnimatedPage) {
    lastAnimatedPage = page;
    pageSwap(pageBody);
  }
  window.scrollTo(0, pageTop);
  pageBody.querySelectorAll("details").forEach((det) => {
    if (open.includes(det.closest(".card")?.querySelector(".provider-name")?.textContent)) det.open = true;
  });
}

async function loadFeed() {
  try {
    const d = await api(`/api/ledger?name=${encodeURIComponent(ledger.track)}`);
    ledger.entries = d.entries || [];
    set("feed", ledger.entries);
  } catch (e) { /* The connection state already shows this. */ }
}

async function loadThreads(force = false) {
  if (threads.loading || (!force && Date.now() - threads.loadedAt < 30000)) return;
  threads.loading = true;
  try {
    const scope = threads.scope || "subway";
    const data = await api(`/api/threads?scope=${encodeURIComponent(scope)}&limit=200`);
    threads.chats = data.threads || data.chats || [];
    threads.meta = {};
    for (const t of threads.chats) {
      if (t && t.id) threads.meta[t.id] = t.meta || {};
    }
    threads.scope = data.scope || scope;
    threads.loadedAt = Date.now();
    threads.error = "";
  } catch (e) {
    threads.error = String(e);
    threads.loadedAt = Date.now();
  } finally {
    threads.loading = false;
    if (currentPage() === "threads") render();
  }
}

async function loadTerrain(force = false) {
  if (terrainState.loading || (!force && Date.now() - terrainState.loadedAt < 60000)) return;
  terrainState.loading = true;
  try {
    const data = await api("/api/worker_activity?days=365");
    terrainState.rows = data.activity || [];
    terrainState.loadedAt = Date.now();
    terrainState.error = "";
  } catch (e) {
    terrainState.error = String(e);
    terrainState.loadedAt = Date.now();
  } finally {
    terrainState.loading = false;
    if (currentPage() === "overview") document.dispatchEvent(new Event("subway:rerender"));
  }
}

async function loadTerminals(force = false) {
  if (terminalsState.loading || (!force && Date.now() - terminalsState.loadedAt < 15000)) return;
  terminalsState.loading = true;
  try {
    const data = await api("/api/terminals");
    terminalsState.rows = data.terminals || [];
    terminalsState.sharing = Boolean(data.sharing);
    terminalsState.loadedAt = Date.now();
    terminalsState.error = "";
  } catch (e) {
    terminalsState.error = String(e);
    terminalsState.loadedAt = Date.now();
  } finally {
    terminalsState.loading = false;
    if (currentPage() === "terminals") render();
  }
}

function switchTrack(name) {
  ledger.track = name;
  ledger.entries = [];
  render();
  loadFeed();
}

function updateBadges(d) {
  const waiting = (d.permissions || []).length + (d.workers || []).filter((w) => w.status === "pending_approval").length;
  const { badge } = nav.workers;
  badge.hidden = !waiting;
  badge.textContent = waiting;
  document.title = waiting ? `(${waiting}) Subway` : "Subway";
}

let inFlight = null;
let refreshAgain = false;
let modelsRev = ""; let modelsTrackCache = null;
function refresh() {
  if (inFlight) {
    refreshAgain = true;
    return inFlight;
  }
  inFlight = (async () => {
    const t0 = performance.now();
    try {
      let ping = 0;
      const [, d] = await Promise.all([
        api("/api/ping").then(() => { ping = Math.round(performance.now() - t0); }),
        api(`/api/status?models_rev=${encodeURIComponent(modelsRev)}`)
      ]);
      if (d && d.models_track) { modelsTrackCache = d.models_track; modelsRev = d.models_track_rev || ""; }
      else if (d && modelsTrackCache) { d.models_track = modelsTrackCache; }
      const { timestamp, time_iso: _iso, fetched_at: _fa, ...stable } = d;
      /* The rest of the page only needs a re-render when the data, or the minute, changes. */
      set("status", d);
      set("slice", { ...stable, minute: Math.floor(timestamp / 60) });
      linkDot.className = "dot ok";
      linkText.textContent = `Ping ${ping} ms`;
      alertBar.hidden = true;
      pingLowLimits(d.settings?.low_limits || []);
      pingBudgets(d.settings?.budgets || []);
    } catch (e) {
      linkDot.className = "dot bad";
      linkText.textContent = "Disconnected";
      alertBar.textContent = "The Subway server isn't responding, so what's shown may be out of date.";
      alertBar.hidden = false;
    } finally {
      inFlight = null;
      if (refreshAgain) {
        refreshAgain = false;
        refresh();
      }
    }
  })();
  return inFlight;
}

/* Pings once per limit window at each level, then stays quiet until it resets. */
function pingLowLimits(items) {
  let seen = {};
  try { seen = JSON.parse(localStorage.getItem("subway-pings") || "{}"); } catch (e) { seen = {}; }
  for (const item of items) {
    const level = item.left_pct <= 10 ? 10 : 20; /* Once at 20%, and once more at 10%. */
    const key = `${item.provider}|${item.pool}|${item.label}|${Math.round(item.resets_at || 0)}|${level}`;
    if (seen[key]) continue;
    seen[key] = 1;
    const reset = item.resets_at ? ` Resets in ${duration(item.resets_at - Date.now() / 1000)}.` : "";
    notify({ message: `${item.provider}: ${item.label} limit at ${Math.round(item.left_pct)}%.${reset}`, kind: "warn" });
  }
  try { localStorage.setItem("subway-pings", JSON.stringify(seen)); } catch (e) { /* Private windows can refuse storage. */ }
}

export function pingBudgets(data) {
  const items = Array.isArray(data) ? data : (data?.settings?.budgets || []);
  let seen = {};
  try { seen = JSON.parse(localStorage.getItem("subway-pings") || "{}"); } catch (e) { seen = {}; }
  for (const b of items) {
    if (b.status !== "warning" && b.status !== "reached") continue;
    const key = `budget|${b.scope}|${b.status}|${b.limit}`;
    if (seen[key]) continue;
    seen[key] = 1;
    const x = tokens(b.used || 0);
    const y = tokens(b.limit || 0);
    if (b.status === "warning") {
      notify({ message: `Budget almost gone: ${b.scope} at ${x} of ${y}. Add more on Overview.`, kind: "warn" });
    } else if (b.status === "reached") {
      notify({ message: `Budget reached: ${b.scope} at ${x} of ${y}. Work has stopped. Add more on Overview.`, kind: "bad" });
    }
  }
  try { localStorage.setItem("subway-pings", JSON.stringify(seen)); } catch (e) { /* Private windows can refuse storage. */ }
}

async function statusLoop() {
  if (!document.hidden) await refresh();
  setTimeout(statusLoop, POLL_MS);
}
async function feedLoop() {
  if (!document.hidden && currentPage() === "ledger") await loadFeed();
  setTimeout(feedLoop, FEED_MS);
}

let lastTracksJson = null;
let lastModelsJson = "";
let lastWorkersSliceJson = null;
let lastThreadsJson = null;
let lastOverviewJson = null;
let lastPage = null;

function updateFeedInPlace() {
  const feed = pageBody.querySelector(".feed");
  if (!feed) {
    render();
    return;
  }
  const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40;
  const feedTop = feed.scrollTop;
  const newChildren = renderLedgerEntries(ledger.entries);
  feed.replaceChildren(...[newChildren].flat(Infinity).filter(Boolean));
  feed.scrollTop = atBottom ? feed.scrollHeight : feedTop;
}

let tetherHandle = null;
function syncTethers(page, slice) {
  if (tetherHandle && (!tetherHandle.el || !document.contains(tetherHandle.el))) {
    unmountTethers();
    tetherHandle = null;
  }
  if (page === "threads") {
    const host = document.querySelector("#content .tt-tether-host");
    if (host) {
      if (!tetherHandle) {
        tetherHandle = mountTethers(host, { docked: true, status: slice, anchor: host.parentElement });
      } else {
        tetherHandle.update(slice);
      }
    }
  } else if (tetherHandle) {
    unmountTethers();
    tetherHandle = null;
  }
}

buildShell();
applyTheme(storedTheme());
applyRail(storedRail());
subscribe("slice", (slice) => {
  const page = currentPage();
  const pageChanged = page !== lastPage;
  lastPage = page;
  syncTethers(page, slice);
  if (page === "models") {
    const modelsSlice = {
      settings: {
        models: slice?.settings?.models,
        benchmarks: slice?.settings?.benchmarks,
      },
      quotas: {
        providers: (slice?.quotas?.providers || []).map((p) => [p.id, p.name]),
      },
      exhausted_models: slice?.exhausted_models,
      models_track: slice?.models_track,
    };
    const modelsJson = JSON.stringify(modelsSlice);
    if (pageChanged || (modelsJson !== lastModelsJson && !hasModelEdits())) {
      lastModelsJson = modelsJson;
      render();
    }
  } else if (page === "ledger") {
    const tracks = (slice && slice.tracks) || {};
    const tracksSummary = JSON.stringify(Object.entries(tracks).map(([k, v]) => [k, v?.count || 0]).sort());
    if (tracksSummary !== lastTracksJson) {
      lastTracksJson = tracksSummary;
      render();
    }
  } else if (page === "workers") {
    const workersSlice = {
      workers: slice?.workers,
      permissions: slice?.permissions,
      settings: slice?.settings,
      history: slice?.history,
    };
    const workersJson = JSON.stringify(workersSlice);
    if (openLogs.size > 0 && workersJson === lastWorkersSliceJson) {
      // Data unchanged and inline log open: skip re-rendering
    } else {
      lastWorkersSliceJson = workersJson;
      render();
    }
  } else if (page === "threads") {
    /* Keep the thread data refreshing, but rebuild the DOM only when its own state changes;
       otherwise the canvas would be replaced every second and jump the user while scrolling. */
    loadThreads();
    const sig = JSON.stringify({ view: threads.view, sel: threads.graphSel, n: threads.chats.length });
    if (pageChanged || sig !== lastThreadsJson) {
      lastThreadsJson = sig;
      render();
    }
  } else if (page === "overview") {
    /* The overview holds animated canvases, so it must not rebuild on every
       poll. Re-render only when something it shows has actually changed;
       timestamps and latencies are bucketed so steady state stays still. */
    const provs = (slice && slice.quotas && slice.quotas.providers) || [];
    const sig = JSON.stringify({
      bucket: Math.floor(Date.now() / 60000), /* at most one refresh a minute */
      providers: provs.map((p) => [p.id, p.state, Array.isArray(p.tokens && p.tokens.hourly) ? p.tokens.hourly.slice(-2) : (p.tokens || null)]),
      locks: Object.keys((slice && slice.locks) || {}).length,
      workers: ((slice && slice.workers) || []).length,
      permissions: ((slice && slice.permissions) || []).length,
    });
    if (pageChanged || sig !== lastOverviewJson) {
      lastOverviewJson = sig;
      render();
    }
  } else {
    render();
  }
  updateBadges(get("status") || {});
});
subscribe("feed", () => {
  if (currentPage() === "ledger") updateFeedInPlace();
});
window.addEventListener("hashchange", () => {
  lastTracksJson = null;
  lastWorkersSliceJson = null;
  lastThreadsJson = null;
  render();
  if (currentPage() === "ledger") loadFeed();
  if (currentPage() === "threads") loadThreads();
});
document.addEventListener("subway:rerender", () => { render(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
document.addEventListener("subway:refresh", refresh);
setInterval(tickAll, 1000);
render();
statusLoop();
feedLoop();
listen(refresh);
