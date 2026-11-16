/* Usage page: provider limits and Stats come from the HUD's own modules (rendered in a shadow root with the
   HUD stylesheet, re-themed to the prototype); Diagnostics is new UI built here. */
import { h } from "/hud/lib.js";
import { providerCard } from "/hud/usage.js";
import { statsPage } from "/stats.js";
import { barChart } from "/hud/charts.js";

const ROUTE = "usage";
const RANK = { live: 0, stale: 1, detected: 2, unavailable: 3 };
const STEP = 5;               // seconds between diagnostics samples
const WINDOWS = [["5m", 300], ["15m", 900], ["30m", 1800], ["1h", 3600]];
const GROUP = { backend: ["Backend + agents", "#2C86B0"], workers: ["Workers", "#D4A64A"], hud: ["HUD + monitor", "#74A5A9"] };
const GROUP_KEY = { "Backend + agents": "backend", "Workers": "workers", "HUD + monitor": "hud" };
let tlRows = [], tlMax = 100;

/* ---------- icons: 24px stroke set, same weight as the composer's ---------- */
const P = {
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
  memory: '<rect x="3" y="7" width="18" height="10" rx="2"/><path d="M7 11v2M11 11v2M15 11v2M6 17v3M10 17v3M14 17v3M18 17v3"/>',
  stack: '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
  read: '<path d="M12 4v10m0 0-4-4m4 4 4-4"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
  write: '<path d="M12 14V4m0 0-4 4m4-4 4 4"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
  gauge: '<path d="M4.9 18a9 9 0 1 1 14.2 0"/><path d="m12 13 4-5"/><circle cx="12" cy="13" r="1.4"/>',
  plug: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0V8ZM12 17v4"/>',
  thermo: '<path d="M14 14.8V5a2 2 0 1 0-4 0v9.8a4 4 0 1 0 4 0Z"/><path d="M12 11v6"/>',
  leaf: '<path d="M5 19c0-8 5-14 15-15-1 10-7 15-15 15Z"/><path d="M5 19 13 11"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  pulse: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="m8.5 12.3 2.4 2.4 4.6-5"/>',
  warn: '<path d="M10.3 4.2 2.8 17.5A2 2 0 0 0 4.5 20.5h15a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0Z"/><path d="M12 9.5v4M12 17h.01"/>',
  x: '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
  minus: '<circle cx="12" cy="12" r="9"/><path d="M8.5 12h7"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.6-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.6 4.5L20 16"/><path d="M20 20v-4h-4"/>',
  tree: '<path d="M6 4v14a2 2 0 0 0 2 2h3M6 9h5"/><rect x="13" y="6" width="7" height="6" rx="1.5"/><rect x="13" y="16" width="7" height="6" rx="1.5" transform="translate(0 -1)"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 10v10"/>',
  probe: '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3"/><path d="M7.5 15h9"/>',
  bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/>',
};
const ic = (n, s = 16) => `<svg class="dg-i" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[n]}</svg>`;
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------- formatting ---------- */
const gb = (b) => b == null ? "—" : b >= 1073741824 ? (b / 1073741824).toFixed(1) + " GB" : (b / 1048576).toFixed(0) + " MB";
const rate = (b) => b == null ? "—" : b >= 1048576 ? (b / 1048576).toFixed(1) + " MB/s" : b >= 1024 ? (b / 1024).toFixed(0) + " KB/s" : Math.round(b) + " B/s";
const cpu = (v) => v == null ? "—" : (v >= 100 ? Math.round(v) : v.toFixed(1)) + "%";
const ago = (t) => { if (!t) return "never"; const s = Math.max(0, Math.round(Date.now() / 1000 - t)); return s < 5 ? "just now" : s < 60 ? s + "s ago" : Math.round(s / 60) + "m ago"; };

/* ---------- shared HUD stylesheet in a shadow root ---------- */
let hudCss = null, hudOver = "";
export async function hudSheet() {
  if (hudCss) return hudCss;
  const [a, b] = await Promise.all(["/hud/styles.css", "/stats.css"].map((u) => fetch(u, { cache: "no-store" }).then((r) => r.text())));
  // One <style> per file: each HUD stylesheet ends inside an unclosed block, which would swallow the next file.
  const host = (css) => css
    .replace(/:root\[data-theme="dark"\]|:root:not\(\[data-theme="light"\]\)/g, ":host(.never)")
    .replace(/:root\[data-theme="light"\]|:root:not\(\[data-theme="dark"\]\)/g, ":host")
    .replace(/:root/g, ":host");
  hudCss = [host(a), host(b)];
  hudOver = `:host{display:block;color-scheme:light;--bg:transparent;--surface:#fff;--raised:#F7F9F3;--hover:#EEF3EE;--line:#E1E5DB;--line-soft:#ECEFE7;
      --text:#29444C;--strong:#0F2F3A;--muted:#5E7275;--accent:#2C86B0;--accent-strong:#296A7F;--accent-ink:#fff;
      --accent-soft:rgba(44,134,176,.12);--accent-line:rgba(44,134,176,.4);--radius:12px;
      --font-sans:"Instrument Sans",system-ui,sans-serif;--font-mono:"JetBrains Mono",ui-monospace,monospace;--font-display:var(--font-sans);
      font-family:var(--font-sans);color:var(--text);font-size:14px;line-height:1.45}
      .card{box-shadow:none;border-color:var(--line)}
      /* Limits: pack cards of uneven height into columns instead of rows of the tallest card. */
      .providers{display:flex;gap:14px;align-items:flex-start}
      .prov-col{flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:14px}
      .legend>.gauge-wrap{max-width:170px;max-height:170px;margin-inline:auto}
      .pool{grid-template-columns:150px minmax(0,1fr);gap:20px;align-items:center}
      .pool .gauge-chart{width:150px;max-width:150px}
      .pool .gauge-chart .gauge-value{transform:translateY(14px)}
      .pool .legend{gap:4px}
      .pool .pool-cap{font-size:12px;color:var(--muted);margin-bottom:4px}
      .pool .legend-row{font-size:13px}
      .pool .legend-row+.row-sub{margin:-2px 0 6px 14px}
      :host(.dg-bars) .bc-v{--bc-h:150px}
      .pool .gauge-chart .gauge-value{font-size:52px}
      .stats-head .card-title{display:none}
      .icon-btn{border-radius:var(--radius)}
      @media (min-width:1100px){
        .stats-body{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:start}
        .stats-body>*{grid-column:1/-1}
        .stats-body>.stats-breakdown{grid-column:auto;max-height:340px;overflow:auto}
        .stats-body>.stats-totals-row{grid-column:1}.stats-body>.bc{grid-column:2}
      }`;
  return hudCss;
}
/* charts.js puts its CSS in document.head the first time a chart draws; shadow roots don't see it, so mirror it. */
const chartRoots = new Set();
const chartCss = () => document.getElementById("subway-chart-style")?.textContent || "";
new MutationObserver(() => {
  const t = chartCss();
  for (const c of chartRoots) { if (!c.isConnected) chartRoots.delete(c); else if (c.textContent !== t) c.textContent = t; }
}).observe(document.head, { childList: true });
export function shadowHost(cls) {
  const host = document.createElement("div");
  host.className = cls;
  const root = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  root.append(style);
  const style2 = document.createElement("style"), chart = document.createElement("style"), over = document.createElement("style");
  root.append(style2, chart, over);
  chart.textContent = chartCss();
  chartRoots.add(chart);
  hudSheet().then(([css, css2]) => { style.textContent = css; style2.textContent = css2; over.textContent = hudOver; host.classList.add("ready"); });
  return { host, root };
}

/* ---------- Usage (the HUD's provider cards) ---------- */
// Placement order: the first row holds the tools with limits; each later card goes to the shortest column,
// so OpenCode sits under Claude and nothing leaves a tall gap.
const PLACE = ["claude", "antigravity", "local", "pi", "opencode", "copilot"];
const UNDER = { opencode: "claude" };   // asked for: OpenCode directly under Claude
let PROVIDERS = [...PLACE];
const placeRank = (p) => { const i = PROVIDERS.indexOf(p.id); return i < 0 ? PROVIDERS.length : i; };
async function loadProviders(){try{
  const r=await fetch("/api/models",{cache:"no-store"});
  if(!r.ok)throw new Error("HTTP "+r.status);
  const j=await r.json();
  const arr=Array.isArray(j)?j:(j&&Array.isArray(j.models)?j.models:[]).filter(m=>m&&m.provider);
  const seen=[];for(const m of arr){const p=m.provider;if(!seen.includes(p))seen.push(p);}
  if(seen.length)PROVIDERS=[...PLACE,...seen.filter(p=>!PLACE.includes(p))];
}catch(e){}
  return PROVIDERS;}
function masonry(box, cards) {
  const w = box.clientWidth || 1200;
  const n = Math.max(1, Math.min(4, Math.floor((w + 14) / 314)));
  if (box._n === n && box._cards === cards) return;
  box._n = n; box._cards = cards;
  const cols = Array.from({ length: n }, () => h("div", { class: "prov-col" }));
  box.replaceChildren(...cols);
  const at = {};
  for (const { id, el } of cards) {
    const col = (UNDER[id] && at[UNDER[id]]) || cols.reduce((m, c) => (c.offsetHeight < m.offsetHeight ? c : m));
    col.append(el); at[id] = col;
  }
}
async function usageSection(slot) {
  const { host, root } = shadowHost("us-hud");
  slot.replaceChildren(host);
  const body = h("div", { class: "providers" }, h("div", { class: "empty" }, "Loading limits…"));
  root.append(body);
  /* The ring's caption ("Weekly left") moves out of the ring to the top of the text column beside it. */
  const capsBeside = (card) => {
    for (const pool of card.querySelectorAll(".pool")) {
      const cap = pool.querySelector(".gauge-caption"), leg = pool.querySelector(".legend");
      if (!cap || !leg || !cap.textContent) continue;
      const el = h("div", { class: "pool-cap" }, `Ring: ${cap.textContent}`);
      const name = leg.querySelector(".pool-name");
      name ? name.after(el) : leg.prepend(el);
      cap.textContent = "";
    }
    return card;
  };
  try {
    const [d] = await Promise.all([fetch("/api/status?view=next", { cache: "no-store" }).then((r) => r.json()), hudSheet()]);
    const ps = d?.quotas?.providers || [];
    if (!ps.length) { body.replaceChildren(h("div", { class: "empty" }, "No LLM tools found on this machine.")); return; }
    await loadProviders();
    const cards = [...ps].sort((a, b) => (RANK[a.state] ?? 2) - (RANK[b.state] ?? 2) || placeRank(a) - placeRank(b)).map((p) => ({ id: p.id, el: capsBeside(providerCard(p, d.timestamp)) }));
    await new Promise(requestAnimationFrame);   // the stylesheet is in; measure with real heights
    masonry(body, cards);
    new ResizeObserver(() => masonry(body, cards)).observe(body);
  } catch (e) {
    body.replaceChildren(h("div", { class: "empty" }, `Couldn't load limits: ${e.message || e}`));
  }
}

/* ---------- Stats (the HUD's stats page) ---------- */
/* ---------- Models (the HUD's models page) ---------- */
async function modelsSection(slot) {
  const { host, root } = shadowHost("us-hud");
  slot.replaceChildren(host);
  const box = h("div", {}, h("div", { class: "empty" }, "Loading models…"));
  root.append(box);
  try {
    const [{ modelsPage }, d] = await Promise.all([import("/hud/views.js"), fetch("/api/status?view=next&models=1", { cache: "no-store" }).then((r) => r.json())]);
    const out = modelsPage(d);
    box.replaceChildren(...(Array.isArray(out) ? out : [out]).filter(Boolean));
  } catch (e) {
    box.replaceChildren(h("div", { class: "empty" }, `Couldn't load models: ${e.message || e}`));
  }
}

/* Budgets: the HUD's own card, view only here; budgets are changed in the HUD. */
async function budgetsSection(slot) {
  const { host, root } = shadowHost("us-hud");
  slot.replaceChildren(host);
  root.append(h("style", {}, ".bud-ro input,.bud-ro button,.bud-ro form{display:none!important}.bud-note{margin:0 0 10px;color:var(--muted);font-size:13px}"));
  const box = h("div", { class: "bud-ro" }, h("div", { class: "empty" }, "Loading budgets…"));
  root.append(box);
  try {
    const [{ budgetsCard }, d] = await Promise.all([import("/hud/views.js"), fetch("/api/status?view=next", { cache: "no-store" }).then((r) => r.json())]);
    const card = budgetsCard(d);
    box.replaceChildren(h("p", { class: "bud-note" }, "Budgets are changed in the HUD."), card || h("div", { class: "empty" }, "No budgets set"));
  } catch (e) {
    box.replaceChildren(h("div", { class: "empty" }, `Couldn't load budgets: ${e.message || e}`));
  }
}

/* Stats: token savings first (its own shadow host), then the HUD's stats page. */
function statsSection(slot) {
  const tok = document.createElement("div"), rest = document.createElement("div");
  rest.style.marginTop = "16px";
  slot.replaceChildren(tok, rest);
  tokensSection(tok);
  const { host, root } = shadowHost("us-hud");
  rest.replaceChildren(host);
  root.append(statsPage({}));
}

/* ---------- Diagnostics: new UI ---------- */
const dg = { data: null, digest: null, error: "", loading: false, local: [], win: 900, view: "tree", live: true, timer: 0, es: null, tl: null, tick: 0 };

async function loadDiag() {
  dg.loading = true; paintDiag();
  try {
    const r = await fetch("/api/diagnostics", { cache: "no-store" });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
    dg.data = d; dg.error = "";
    if (!Array.isArray(d.history)) {   // until the HUD keeps history itself, keep this page's own samples
      const g = {}; for (const x of d.groups || []) g[GROUP_KEY[x.name] || x.name] = x.cpu;
      dg.local.push({ t: d.sampled_at, cpu: d.cpu_pct, rss: d.rss_bytes, read_bps: d.read_bps, write_bps: d.write_bps, procs: d.process_count, groups: g });
      if (dg.local.length > 720) dg.local.shift();
    }
  } catch (e) { dg.error = e.message || String(e); }
  dg.loading = false; paintDiag();
}
async function loadDigest() {
  try { const r = await fetch("/api/worker_digest", { cache: "no-store" }); dg.digest = (await r.json()).items || []; }
  catch { dg.digest = []; }
  paintDiag();
}
const history = () => (Array.isArray(dg.data?.history) ? dg.data.history : dg.local);

const sparkSeen = new Map();
function liveSpark(key, vals, color) {
  const v = vals.filter((x) => x != null);
  if (v.length < 2) return `<div class="dg-spark-wrap"><svg class="dg-spark" viewBox="0 0 100 36" preserveAspectRatio="none" aria-hidden="true"><path d="M0 34H100" stroke="#E1E5DB" stroke-width="2" fill="none" vector-effect="non-scaling-stroke"/></svg></div>`;
  const max = Math.max(...v) || 1, min = Math.min(...v), span = max - min || max || 1;
  const pts = v.map((x, i) => [(i / (v.length - 1)) * 100, 34 - ((x - min) / span) * 30]);
  /* monotone-X cubic (Fritsch–Carlson) tangents */
  const n = pts.length, dx = [], ms = [], t = [];
  for (let i = 0; i < n - 1; i++) { dx.push(pts[i + 1][0] - pts[i][0]); ms.push((pts[i + 1][1] - pts[i][1]) / dx[i]); }
  t.push(ms[0]);
  for (let i = 1; i < n - 1; i++) t.push(ms[i - 1] * ms[i] <= 0 ? 0 : 2 / (1 / ms[i - 1] + 1 / ms[i]));
  t.push(ms[n - 2]);
  let line = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i];
    line += `C${(pts[i][0] + h / 3).toFixed(2)} ${(pts[i][1] + t[i] * h / 3).toFixed(2)} ${(pts[i + 1][0] - h / 3).toFixed(2)} ${(pts[i + 1][1] - t[i + 1] * h / 3).toFixed(2)} ${pts[i + 1][0].toFixed(1)} ${pts[i + 1][1].toFixed(1)}`;
  }
  const gid = `dg-grad-${key}`;
  const grow = v.length > (sparkSeen.get(key) || 0);
  sparkSeen.set(key, v.length);
  const [ex, ey] = pts[n - 1];
  return `<div class="dg-spark-wrap"><svg class="dg-spark" viewBox="0 0 100 36" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="${gid}" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="${color}" stop-opacity=".18"/><stop offset="100%" stop-color="${color}" stop-opacity="0"/></linearGradient></defs><g${grow ? ' class="dg-slide"' : ""}><path d="${line}L100 36L0 36Z" fill="url(#${gid})"/><path d="${line}" stroke="${color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></g></svg><span class="dg-dot" style="left:${ex.toFixed(2)}%;top:${(ey / 36 * 100).toFixed(2)}%;color:${color}"></span></div>`;
}

function tiles(d, hist) {
  const last = hist.slice(-36);
  const t = [
    ["cpu", "CPU", cpu(d.cpu_pct), d.cpu_pct != null ? `${(d.cpu_pct / 100).toFixed(1)} cores busy` : "", last.map((x) => x.cpu), "#2C86B0"],
    ["memory", "Memory", gb(d.rss_bytes), "resident, all processes", last.map((x) => x.rss), "#296A7F"],
    ["stack", "Processes", d.process_count ?? "—", "on this Mac", last.map((x) => x.procs), "#74A5A9"],
    ["read", "Disk read", rate(d.read_bps), d.read_bps == null ? "needs a second sample" : "per second", last.map((x) => x.read_bps), "#2692A6"],
    ["write", "Disk write", rate(d.write_bps), d.write_bps == null ? "needs a second sample" : "per second", last.map((x) => x.write_bps), "#D4A64A"],
    ["gauge", "CPU speed limit", d.cpu_speed_limit != null ? d.cpu_speed_limit + "%" : "Unknown", "not reported on this Mac", [], "#5E7275"],
  ];
  return `<div class="dg-tiles">${t.map(([i, l, v, s, h, c]) => `<div class="dg-tile">
    <div class="dg-tile-h"><span class="dg-ico" style="--c:${c}">${ic(i)}</span><span>${l}</span></div>
    <div class="dg-big">${esc(v)}</div><div class="dg-sub">${esc(s)}</div>${h.length ? liveSpark(l, h, c) : ""}</div>`).join("")}</div>`;
}

function overhead(d) {
  const gs = (d.groups || []).map((g) => ({ ...g, key: GROUP_KEY[g.name] || "backend" }));
  const tc = d.cpu_pct || 0, tr = d.rss_bytes || 0;
  const bar = (field, total, fmt) => {
    const used = gs.reduce((n, g) => n + (g[field] || 0), 0);
    return `<div class="dg-split" role="img" aria-label="${field === "cpu" ? "CPU" : "Memory"} split">${gs.map((g) => `<span style="flex:${Math.max(g[field] || 0, 0)};background:${GROUP[g.key][1]}" title="${esc(g.name)}: ${fmt(g[field])}"></span>`).join("")}<span class="rest" style="flex:${Math.max(total - used, 0)}"></span></div>`;
  };
  return `<div class="dg-card dg-c-over"><div class="dg-head"><h3>Subway's own overhead</h3><span class="dg-note">share of this Mac's total</span></div>
    <div class="dg-row2"><div><div class="dg-lab">CPU</div>${bar("cpu", tc, cpu)}</div><div><div class="dg-lab">Memory</div>${bar("rss", tr, gb)}</div></div>
    <div class="dg-legend">${gs.map((g) => `<div class="dg-lg"><i style="background:${GROUP[g.key][1]}"></i><b>${esc(g.name)}</b>
      <span>${g.processes} proc · ${cpu(g.cpu)} · ${gb(g.rss)}</span></div>`).join("")}</div></div>`;
}

function timeline(hist) {
  const now = hist.length ? hist[hist.length - 1].t : Date.now() / 1000;
  const pts = hist.filter((x) => x.t > now - dg.win);
  const cols = 40, span = dg.win / cols, bins = Array.from({ length: cols }, () => null);
  for (const p of pts) { const i = Math.min(cols - 1, Math.floor((p.t - (now - dg.win)) / span)); const b = bins[i] || (bins[i] = { cpu: 0, n: 0, g: {} }); b.cpu += p.cpu || 0; b.n++; for (const [k, v] of Object.entries(p.groups || {})) b.g[k] = (b.g[k] || 0) + (v || 0); }
  const max = Math.max(1, ...bins.filter(Boolean).map((b) => b.cpu / b.n));
  const seg = WINDOWS.map(([l, s]) => `<button type="button" data-win="${s}" aria-pressed="${s === dg.win}">${l}</button>`).join("");
  tlMax = Math.ceil(max / 50) * 50;
  tlRows = bins.map((b, i) => {
    if (!b) return null;
    const tot = b.cpu / b.n;
    const when = new Date((now - dg.win + (i + 1) * span) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return { id: "b" + i, label: when, value: tot, display: cpu(tot), color: "#2C86B0",
      sub: `Backend ${Math.round((b.g.backend || 0) / b.n)}% · Workers ${Math.round((b.g.workers || 0) / b.n)}% · HUD ${Math.round((b.g.hud || 0) / b.n)}%` };
  }).filter(Boolean);
  const src = Array.isArray(dg.data?.history) ? `one sample every ${dg.data.history_step || STEP}s, kept by the HUD` : `sampled by this page every ${STEP}s while it is open`;
  return `<div class="dg-card dg-c-time"><div class="dg-head"><h3>Resource timeline</h3><div class="dg-seg" role="group" aria-label="Window">${seg}</div></div>
    <div aria-label="CPU over the last ${WINDOWS.find((w) => w[1] === dg.win)[0]}">${pts.length ? `<div class="dg-chart-slot"></div>` : `<div class="dg-empty">${ic("pulse", 18)}<span>Collecting samples…</span></div>`}</div>
    <div class="dg-axis"><span>${WINDOWS.find((w) => w[1] === dg.win)[0]} ago</span><span>peak ${cpu(max)}</span><span>now</span></div>
    <div class="dg-note">${pts.length} samples · ${src}. Colours are Subway's share; grey is everything else.</div></div>`;
}

function processes(d) {
  const rows = Array.isArray(d.processes) ? d.processes : null;
  const toggle = `<div class="dg-seg" role="group" aria-label="View">${[["tree", "Tree"], ["table", "Table"]].map(([k, l]) => `<button type="button" data-view="${k}" aria-pressed="${dg.view === k}">${ic(k, 14)}${l}</button>`).join("")}</div>`;
  if (!rows) return `<div class="dg-card dg-c-proc"><div class="dg-head"><h3>Live processes</h3>${toggle}</div><div class="dg-empty">${ic("tree", 18)}<span>The HUD doesn't list processes yet. They show up here once the diagnostics update lands.</span></div></div>`;
  const byPid = new Map(rows.map((r) => [r.pid, r]));
  let list = rows;
  if (dg.view === "tree") {
    const kids = new Map(); const roots = [];
    for (const r of rows) (byPid.has(r.ppid) ? (kids.get(r.ppid) || kids.set(r.ppid, []).get(r.ppid)) : roots).push(r);
    list = []; const walk = (r, depth) => { list.push({ ...r, depth }); for (const k of kids.get(r.pid) || []) walk(k, depth + 1); };
    for (const r of roots) walk(r, 0);
  }
  const maxCpu = Math.max(1, ...rows.map((r) => r.cpu || 0));
  return `<div class="dg-card dg-c-proc"><div class="dg-head"><h3>Live processes</h3><span class="dg-note">${rows.length} Subway processes</span>${toggle}</div>
    <div class="dg-table" role="table"><div class="dg-tr dg-th" role="row"><span>Process</span><span>Group</span><span>CPU</span><span>Memory</span><span>PID</span></div>
    ${list.map((r) => `<div class="dg-tr" role="row"><span class="dg-proc" style="--d:${r.depth || 0}" title="${esc(r.cmd)}">${r.depth ? '<i class="dg-elbow"></i>' : ""}<b>${esc(r.name)}</b><small>${esc(r.worker_id || r.cmd)}</small></span>
      <span><em class="dg-chip" style="--c:${(GROUP[r.group] || GROUP.backend)[1]}">${esc((GROUP[r.group] || [r.group])[0])}</em></span>
      <span class="dg-num"><i class="dg-meter"><b style="width:${((r.cpu || 0) / maxCpu) * 100}%"></b></i>${cpu(r.cpu)}</span><span class="dg-num">${gb(r.rss)}</span><span class="dg-num dim">${r.pid}</span></div>`).join("")}</div></div>`;
}

function pill(status) {
  const m = { ok: ["Healthy", "check"], warn: ["Warning", "warn"], fail: ["Failing", "x"], unsupported: ["Not reported", "minus"] }[status] || ["Unknown", "minus"];
  return `<span class="dg-pill" data-s="${status}">${ic(m[1], 13)}${m[0]}</span>`;
}
function hostCard(d) {
  const hst = d.host || {};
  const therm = hst.thermal && /no thermal warning/i.test(hst.thermal) ? "nominal" : hst.thermal;
  const up = (hst.uptime || "").match(/up\s+([^,]+(?:,\s*\d+:\d+)?)/);
  const rows = [
    ["plug", "Power", hst.power_source || "Unknown", hst.power_source ? "ok" : "unsupported"],
    ["leaf", "Low power mode", hst.low_power == null ? "Unknown" : hst.low_power ? "On" : "Off", hst.low_power ? "warn" : hst.low_power == null ? "unsupported" : "ok"],
    ["thermo", "Thermal", therm === "nominal" ? "Nominal" : therm || "Unknown", !therm ? "unsupported" : therm === "nominal" ? "ok" : "warn"],
    ["pulse", "Load average", (hst.load_avg || "—").replace(/[{}]/g, "").trim(), hst.load_avg ? "ok" : "unsupported"],
    ["clock", "Uptime", up ? up[1].trim() : "—", hst.uptime ? "ok" : "unsupported"],
  ];
  return `<div class="dg-card dg-c-host"><div class="dg-head"><h3>Host state</h3></div><div class="dg-list">${rows.map(([i, l, v, s]) =>
    `<div class="dg-li"><span class="dg-ico">${ic(i)}</span><span class="dg-li-l">${l}</span><span class="dg-li-v">${esc(v)}</span>${pill(s)}</div>`).join("")}</div></div>`;
}
function collectionCard(d) {
  const cs = (d.collection?.collectors || []).map((c) => ({ ...c, s: c.status || (c.ok ? "ok" : c.name === "cpu_speed_limit" ? "unsupported" : "fail") }));
  const LABEL = { ps: "Process list", "top-disks": "Disk throughput", "pmset-batt": "Power source", "pmset-therm": "Thermal state", "pmset-lowpower": "Low power mode", loadavg: "Load average", uptime: "Uptime", cpu_speed_limit: "CPU speed limit" };
  return `<div class="dg-card dg-c-coll"><div class="dg-head"><h3>Collection health</h3><span class="dg-note">${d.collection?.ms ?? "—"} ms per sample</span></div><div class="dg-list">${cs.map((c) =>
    `<div class="dg-li"><span class="dg-ico">${ic("probe")}</span><span class="dg-li-l">${esc(LABEL[c.name] || c.name)}<small>${esc(c.detail || c.error || c.name)}</small></span>${pill(c.s)}</div>`).join("")}</div></div>`;
}
function failuresCard() {
  const items = dg.digest;
  const body = items == null ? `<div class="dg-empty">${ic("pulse", 18)}<span>Loading…</span></div>`
    : !items.length ? `<div class="dg-empty">${ic("check", 18)}<span>No repeated worker failures.</span></div>`
    : `<div class="dg-list">${items.map((x) => `<div class="dg-li dg-fail"><span class="dg-ico" data-s="${/stall|time/.test(x.pattern) ? "warn" : "fail"}">${ic(/stall|time/.test(x.pattern) ? "clock" : "bolt")}</span>
        <span class="dg-li-l"><b>${esc(x.pattern)}</b> on ${esc(x.provider)}/${esc(x.model)}<small>${(x.files || []).slice(0, 3).map(esc).join(" · ") || "no files touched"}${x.example_id ? ` · e.g. ${esc(x.example_id)}` : ""}</small></span>
        <span class="dg-count">${x.count}×</span></div>`).join("")}</div>`;
  return `<div class="dg-card dg-c-fail"><div class="dg-head"><h3>Latest failures</h3><span class="dg-note">workers, grouped by pattern</span></div>${body}</div>`;
}

let diagEl = null;
function paintDiag() {
  if (!diagEl) return;
  const d = dg.data;
  if (!dg.tick) dg.tick = setInterval(() => { if (!diagEl?.isConnected) { clearInterval(dg.tick); dg.tick = 0; return; } if (document.hidden || !dg.data) return; const s = diagEl.querySelector(".dg-stamp"); if (s) s.textContent = `Sampled ${ago(dg.data.sampled_at)}`; }, 1000);
  const head = `<div class="dg-top"><div><p>What Subway costs this Mac, and whether the probes behind these numbers are working.</p></div>
    <div class="dg-actions">${d ? `<span class="dg-pill big" data-s="${d.healthy ? "ok" : "fail"}">${ic(d.healthy ? "check" : "x", 14)}${d.healthy ? "Healthy" : "Issues found"}</span>` : ""}
    <button type="button" class="dg-btn" data-live aria-pressed="${dg.live}"><span class="dg-dot"></span>${dg.live ? "Live" : "Paused"}</button>
    <button type="button" class="dg-btn" data-refresh aria-busy="${dg.loading}" ${dg.loading ? "disabled" : ""}>${ic("refresh", 14)}Refresh</button></div></div>
    <div class="dg-stamp">${d ? `Sampled ${ago(d.sampled_at)}` : ""}</div>`;
  if (!d) {
    diagEl.innerHTML = head + (dg.error ? `<div class="dg-card dg-err" role="alert">${ic("warn", 18)}<span>Couldn't run diagnostics: ${esc(dg.error)}</span></div>`
      : `<div class="dg-card dg-empty">${ic("pulse", 18)}<span>Running the probes…</span></div>`);
    return;
  }
  const hist = history();
  diagEl.innerHTML = head + (dg.error ? `<div class="dg-card dg-err" role="alert">${ic("warn", 18)}<span>Last refresh failed: ${esc(dg.error)}. Showing the previous sample.</span></div>` : "")
    + tiles(d, hist) + overhead(d) + timeline(hist) + processes(d) + hostCard(d) + collectionCard(d) + failuresCard();
  const slot = diagEl.querySelector(".dg-chart-slot");
  if (slot && tlRows.length) {
    if (!dg.tl) dg.tl = shadowHost("us-hud dg-bars");
    const { host, root } = dg.tl;
    if (host.parentNode !== slot) slot.replaceChildren(host);
    root.querySelectorAll(":scope > :not(style)").forEach((n) => n.remove());
    root.append(barChart({ id: "dg-tl", orientation: "vertical", rows: tlRows, max: tlMax, format: (v) => cpu(v), animateKey: String(dg.win), tooltip: (r) => h("div", {}, h("b", {}, r.label + " · " + r.display), h("div", {}, r.sub)), emptyText: "Collecting samples…" }));
  }
}
function onDiagClick(e) {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.win) { dg.win = +b.dataset.win; paintDiag(); }
  else if (b.dataset.view) { dg.view = b.dataset.view; paintDiag(); }
  else if ("refresh" in b.dataset) loadDiag();
  else if ("live" in b.dataset) { dg.live = !dg.live; schedule(); paintDiag(); }
}
function schedule() {
  clearInterval(dg.timer); dg.timer = 0;
  if (dg.es) { dg.es.close(); dg.es = null; }
  if (!dg.live || !diagEl?.isConnected) return;
  if (typeof EventSource === "undefined") {
    dg.timer = setInterval(() => { if (!diagEl?.isConnected) { clearInterval(dg.timer); dg.timer = 0; return; } if (!document.hidden) loadDiag(); }, STEP * 1000);
    return;
  }
  const es = new EventSource("/api/diagnostics/stream");
  dg.es = es;
  es.addEventListener("diag", (ev) => {
    if (!diagEl?.isConnected) { schedule(); return; }
    try {
      const d = JSON.parse(ev.data);
      dg.data = d; dg.error = ""; dg.loading = false;
      const hist = Array.isArray(d?.history) ? d.history : null;
      if (!hist && d && !dg.local.some((x) => x?.t === d.t)) { dg.local.push(d); if (dg.local.length > 720) dg.local.shift(); }
      paintDiag();
    } catch { /* malformed sample: ignore */ }
  });
  es.onerror = () => {
    if (es.readyState === 2 && diagEl?.isConnected) { // closed: fall back to polling
      dg.es = null;
      dg.timer = setInterval(() => { if (!diagEl?.isConnected) { clearInterval(dg.timer); dg.timer = 0; return; } if (!document.hidden) loadDiag(); }, STEP * 1000);
    }
  };
}

let pushBound = false;
function bindPush() {
  if (pushBound) return;
  pushBound = true;
  window.addEventListener("subway:push", (e) => {
    const types = Array.isArray(e?.detail?.types) ? e.detail.types : e?.detail?.type ? [e.detail.type] : [];
    const [r, sub1] = (location.hash.slice(1) || "home").split("/");
    if (r !== ROUTE) return;
    const sub = sub1 === "tokens" ? "stats" : sub1;
    if (sub === "diagnostics") {
      if (types.includes("worker") || types.includes("workers") || types.includes("worker_status")) loadDigest();
      return;
    }
    if (sub === "limits" || sub === "models" || sub === "budgets" || sub === "ledger") {
      if (types.includes("usage") || types.includes("status")) mount();
    }
  });
}
bindPush();

/* ---------- page ---------- */
const CSS = `
:root{--ring-session:#F5C518;--ring-weekly:#5BB8E8;--pv-claude:#C2620F;--pv-antigravity:#5E6B33;--pv-opencode:#111111;--pv-copilot:#0969da;--pv-pi:#7E22CE;--pv-local:#0F766E;--pv-custom:#8b9299;--series-2:#1c1c1e;--series-3:#9a9aa0;--series-claude:var(--pv-claude);--series-antigravity:var(--pv-antigravity);--series-opencode:var(--pv-opencode);--series-copilot:var(--pv-copilot);--series-pi:var(--pv-pi);--series-deepai:var(--pv-pi);--series-local:var(--pv-local)}
.us-wrap{display:flex;flex-direction:column;gap:16px;margin-top:16px;min-width:0}
.us-tabs{display:flex;gap:4px;padding:4px;border:1px solid var(--hair);border-radius:var(--radius);background:var(--surface);width:max-content;max-width:100%;overflow-x:auto;position:sticky;top:8px;z-index:3}
.us-tabs a{padding:7px 14px;border-radius:8px;color:var(--muted);text-decoration:none;font-size:13px;font-weight:500;white-space:nowrap}
.us-tabs a:hover{color:var(--ink)}
.us-tabs a[aria-current="page"]{background:var(--sky-chip-hi);color:var(--sky-ink)}
.us-sec{scroll-margin-top:64px;min-width:0}
.us-sec>h2{font:500 22px/1.2 "Newsreader",Georgia,serif;color:var(--ink);margin:0 0 4px}
.us-sec>p{margin:0 0 14px;color:var(--muted);font-size:14px}
.us-hud{min-height:120px;opacity:0;transition:opacity .2s}.us-hud.ready{opacity:1}
#dg{display:flex;flex-direction:column;gap:14px;color:var(--ink)}
.dg-i{flex:none;display:block}
.dg-top{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;justify-content:space-between}
.dg-top h2{font:500 22px/1.2 "Newsreader",Georgia,serif;margin:0 0 4px}.dg-top p{margin:0;color:var(--muted);font-size:14px;max-width:56ch}
.dg-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.dg-stamp{font:12px "JetBrains Mono",monospace;color:var(--muted);margin-top:-6px}
.dg-btn{display:inline-flex;align-items:center;gap:6px;height:34px;padding:0 12px;border:1px solid var(--hair);border-radius:var(--radius);background:var(--surface);color:var(--ink);font:500 13px "Instrument Sans",sans-serif;cursor:pointer}
.dg-btn:hover{background:var(--cloud)}.dg-btn:focus-visible,.dg-seg button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.dg-btn[aria-busy="true"] .dg-i{animation:dgspin 1s linear infinite}@keyframes dgspin{to{transform:rotate(360deg)}}
.dg-dot{width:8px;height:8px;border-radius:50%;background:#9AA8A9}.dg-btn[aria-pressed="true"] .dg-dot{background:#2E9E6A;box-shadow:0 0 0 3px rgba(46,158,106,.18)}
.dg-card{background:var(--surface);border:1px solid var(--hair);border-radius:var(--radius);padding:16px;min-width:0}
.dg-head{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin-bottom:12px}
.dg-head h3{margin:0;font:600 14px "Instrument Sans",sans-serif;margin-right:auto}
.dg-note{color:var(--muted);font-size:12px}
.dg-head .dg-note{order:2;width:100%}
.dg-tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.dg-tile{background:var(--surface);border:1px solid var(--hair);border-radius:var(--radius);padding:12px 12px 8px;display:flex;flex-direction:column;gap:2px;min-width:0}
.dg-tile-h{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px;font-weight:500}
.dg-ico{display:inline-grid;place-items:center;width:28px;height:28px;border-radius:8px;background:var(--sky-chip);color:var(--sky-ink);flex:none}
.dg-tile .dg-ico{color:var(--c);background:color-mix(in srgb,var(--c) 12%,#fff)}
.dg-big{font:500 22px/1.15 "JetBrains Mono",monospace;margin-top:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dg-sub{color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dg-spark-wrap{position:relative;width:100%;height:36px;margin-top:6px}
.dg-spark{width:100%;height:36px;display:block}
.dg-dot{position:absolute;width:6px;height:6px;border-radius:50%;background:currentColor;transform:translate(-50%,-50%);box-shadow:0 0 0 2px var(--surface)}
.dg-dot::after{content:"";position:absolute;inset:0;border-radius:50%;border:1.5px solid currentColor;animation:dgpulse 1.5s ease-out infinite}
@keyframes dgpulse{from{transform:scale(1);opacity:.5}to{transform:scale(2.4);opacity:0}}
.dg-slide{animation:dgslide .6s cubic-bezier(.2,.8,.2,1)} @keyframes dgslide{from{transform:translateX(calc(100% / 36))}to{transform:none}}
@media (prefers-reduced-motion: reduce){.dg-dot::after{animation:none}.dg-slide{animation:none}}
.dg-row2{display:grid;gap:12px}
.dg-lab{font-size:12px;color:var(--muted);margin-bottom:6px}
.dg-split{display:flex;height:12px;border-radius:999px;overflow:hidden;background:var(--cloud);gap:2px}
.dg-split span{min-width:0}.dg-split .rest{background:#E7EBE3}
.dg-legend{display:grid;gap:8px;margin-top:14px}
.dg-lg{display:flex;align-items:center;gap:8px;font-size:13px;min-width:0}
.dg-lg i{width:10px;height:10px;border-radius:3px;flex:none}.dg-lg b{font-weight:500}
.dg-lg span{margin-left:auto;font:12px "JetBrains Mono",monospace;color:var(--muted);white-space:nowrap}
.dg-seg{display:inline-flex;padding:3px;gap:2px;border:1px solid var(--hair);border-radius:10px;background:var(--cloud)}
.dg-seg button{display:inline-flex;align-items:center;gap:5px;border:0;background:none;padding:5px 10px;border-radius:8px;font:500 12px "Instrument Sans",sans-serif;color:var(--muted);cursor:pointer;min-height:28px}
.dg-seg button[aria-pressed="true"]{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(15,47,58,.08)}
.dg-bars{min-height:180px}
.dg-empty{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:13px;padding:18px 4px;justify-content:center;text-align:left;width:100%}
.dg-err{display:flex;gap:10px;align-items:center;color:#9A3B2E;border-color:#EBC9C0;background:#FBF3F0}
.dg-table{display:flex;flex-direction:column;font-size:13px;overflow-x:auto}
.dg-tr{display:grid;grid-template-columns:minmax(0,1fr) 72px 64px;gap:10px;align-items:center;padding:8px 4px;border-top:1px solid #EEF1EA;min-width:0}
.dg-tr>span:nth-child(2),.dg-tr>span:nth-child(5){display:none}
.dg-th{border-top:0;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;padding-top:0}
.dg-proc{display:flex;flex-direction:column;min-width:0;padding-left:calc(var(--d) * 16px);position:relative}
.dg-proc b{font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dg-proc small{color:var(--muted);font:11px "JetBrains Mono",monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dg-elbow{position:absolute;left:calc(var(--d) * 16px - 12px);top:2px;width:8px;height:10px;border-left:1.5px solid #C9D2C8;border-bottom:1.5px solid #C9D2C8;border-bottom-left-radius:4px}
.dg-chip{font-style:normal;font-size:11px;padding:2px 8px;border-radius:999px;color:var(--c);background:color-mix(in srgb,var(--c) 12%,#fff);white-space:nowrap}
.dg-num{font:12px "JetBrains Mono",monospace;text-align:right;white-space:nowrap;display:flex;align-items:center;gap:6px;justify-content:flex-end}
.dg-num.dim{color:var(--muted)}
.dg-meter{display:none;width:44px;height:4px;border-radius:2px;background:#EEF1EA;overflow:hidden}.dg-meter b{display:block;height:100%;background:var(--accent)}
.dg-pair{display:grid;gap:14px}
.dg-list{display:flex;flex-direction:column}
.dg-li{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid #EEF1EA;min-width:0}.dg-li:first-child{border-top:0}
.dg-li-l{display:flex;flex-direction:column;min-width:0;flex:1;font-size:13px}
.dg-li-l small{color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dg-li-v{font:12px "JetBrains Mono",monospace;color:var(--ink);text-align:right;max-width:40%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dg-pill{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:999px;font:600 10.5px "Instrument Sans",sans-serif;letter-spacing:.05em;text-transform:uppercase;white-space:nowrap;flex:none}
.dg-pill.big{font-size:12px;padding:6px 12px;letter-spacing:.03em}
.dg-pill[data-s="ok"]{color:#1E7A52;background:#E3F3EA}.dg-pill[data-s="warn"]{color:#8A6100;background:#FBF0D6}
.dg-pill[data-s="fail"]{color:#A23A2C;background:#F9E3DE}.dg-pill[data-s="unsupported"]{color:#5E7275;background:#EEF1EA}
.dg-fail .dg-ico[data-s="fail"]{color:#A23A2C;background:#F9E3DE}.dg-fail .dg-ico[data-s="warn"]{color:#8A6100;background:#FBF0D6}
.dg-li-l b{font-weight:600}
.dg-count{font:500 13px "JetBrains Mono",monospace;color:var(--ink)}
@media (min-width:640px){
  .dg-tiles{grid-template-columns:repeat(3,minmax(0,1fr))}
  .dg-row2{grid-template-columns:1fr 1fr}
  .dg-legend{grid-template-columns:repeat(3,minmax(0,1fr))}.dg-lg{flex-wrap:wrap}.dg-lg span{margin-left:18px;width:100%}
  .dg-head .dg-note{order:0;width:auto}
  .dg-tr{grid-template-columns:minmax(0,1fr) 120px 110px 80px 64px}
  .dg-tr>span:nth-child(2),.dg-tr>span:nth-child(5){display:block}.dg-tr>span.dg-num:nth-child(5){display:flex}
  .dg-meter{display:block}
}
@media (min-width:1024px){
  .dg-tiles{grid-template-columns:repeat(6,minmax(0,1fr))}
  /* One viewport: tiles; overhead + timeline; processes + host + probes; failures in two columns. */
  #dg{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px}
  #dg>*{grid-column:1/-1}
  #dg>.dg-c-over{grid-column:span 4}#dg>.dg-c-time{grid-column:span 8}
  #dg>.dg-c-proc{grid-column:span 6}#dg>.dg-c-host,#dg>.dg-c-coll{grid-column:span 3}
  .dg-c-over .dg-row2{grid-template-columns:1fr}.dg-c-over .dg-legend{grid-template-columns:1fr}.dg-c-over .dg-lg{flex-wrap:nowrap}.dg-c-over .dg-lg span{margin-left:auto;width:auto}
  .dg-c-proc .dg-table{max-height:280px;overflow:auto}
  .dg-c-proc .dg-th{position:sticky;top:0;background:var(--surface);z-index:1}
  .dg-c-fail .dg-list{display:grid;grid-template-columns:1fr 1fr;column-gap:28px}
  .dg-c-fail .dg-li:nth-child(2){border-top:0}
}
body.on-usage .us-sec{min-height:0}
@media (prefers-reduced-motion:reduce){.dg-btn[aria-busy="true"] .dg-i{animation:none}.us-hud{transition:none}}
`;

let wrap = null;
function unmount() {
  document.body.classList.remove("on-usage");
  if (dg.es) { dg.es.close(); dg.es = null; }
  wrap?.remove(); wrap = null; diagEl = null; schedule();
}
/* ---------- Tokens: what code packets, skeletons and symbol reads saved ---------- */
const TK_KINDS = { packet: ["Code packets", "#D4A64A"], skel: ["Skeletons", "#B8892E"], read_symbol: ["read_symbol", "#74A5A9"], read_outline: ["read_outline", "#2C86B0"] };
const TK_RANGES = [[7, "7 days"], [30, "30 days"], [90, "90 days"]];
const tk = { days: 7 };
const tnum = (n) => { n = Number(n) || 0; return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e4 ? Math.round(n / 1e3) + "K" : n >= 1e3 ? (n / 1e3).toFixed(1) + "K" : String(n); };
const TK_CSS = `.tk{display:grid;gap:16px}
.tk-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px}
.tk-head h2{margin:0;font-size:15px;font-weight:600;color:var(--ink)}
.tk-head p{margin:2px 0 0;font-size:12px;color:var(--muted)}
.tk-range{display:flex;gap:4px;padding:3px;border:1px solid var(--hair);border-radius:10px}
.tk-range button{all:unset;cursor:pointer;padding:5px 11px;border-radius:7px;font-size:12px;color:var(--muted)}
.tk-range button[aria-pressed="true"]{background:rgba(212,166,74,.16);color:#D4A64A}
.tk-range button:focus-visible{outline:2px solid #D4A64A;outline-offset:1px}
.tk-tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.tk-tile{border:1px solid var(--hair);border-radius:12px;padding:12px 14px;background:var(--surface);min-width:0}
.tk-tile b{display:block;font-size:22px;font-weight:600;color:var(--ink);font-variant-numeric:tabular-nums}
.tk-tile.gold b{color:#D4A64A}
.tk-tile span{font-size:12px;color:var(--muted)}
.tk-card{border:1px solid var(--hair);border-radius:12px;padding:14px;background:var(--surface);min-width:0}
.tk-card h3{margin:0 0 10px;font-size:13px;font-weight:600;color:var(--ink)}
.tk-note{font-size:12px;color:var(--muted);margin:0}
@media (min-width:768px){.tk-tiles{grid-template-columns:repeat(4,minmax(0,1fr))}.tk-two{display:grid;grid-template-columns:3fr 2fr;gap:16px}}`;

async function tokensSection(slot) {
  const { host, root } = shadowHost("us-hud");
  slot.replaceChildren(host);
  root.append(h("style", {}, TK_CSS));
  const box = h("div", { class: "tk" }, h("div", { class: "empty" }, "Loading token savings…"));
  root.append(box);
  let d;
  try {
    const r = await fetch(`/api/token_savings?days=${tk.days}`, { cache: "no-store" });
    if (!r.ok) throw new Error(r.status === 404 ? "the HUD doesn't have /api/token_savings yet (restart it)" : "HTTP " + r.status);
    d = await r.json();
  } catch (e) {
    box.replaceChildren(h("div", { class: "empty" }, `Couldn't load token savings: ${e.message || e}`));
    return;
  }
  const today = d.today || {}, range = d.range || {};
  const uses = Object.values(range.kinds || {}).reduce((s, k) => s + (k.uses || 0), 0);
  const rangeBtns = h("div", { class: "tk-range", role: "group", "aria-label": "Range" },
    ...TK_RANGES.map(([n, l]) => h("button", { type: "button", "aria-pressed": String(n === tk.days), on: { click: () => { tk.days = n; tokensSection(slot); } } }, l)));
  const tile = (v, l, gold) => h("div", { class: "tk-tile" + (gold ? " gold" : "") }, h("b", {}, v), h("span", {}, l));
  const tiles = h("div", { class: "tk-tiles" },
    tile(tnum(today.saved_tokens), "saved today", true),
    tile(tnum(range.saved_tokens), `saved, last ${tk.days} days`),
    tile((range.saved_pct || 0) + "%", "less than reading whole files"),
    tile(tnum(uses), "packets and reads"));
  const series = Array.isArray(range.series) ? range.series : null;
  const daily = h("div", { class: "tk-card" }, h("h3", {}, "Saved per day"),
    !series ? h("p", { class: "tk-note" }, "The per-day chart appears once the backend sends a daily series.")
      : barChart({ id: "tk-daily", orientation: "vertical", animateKey: String(tk.days),
          rows: series.map((p) => ({ id: p.day, label: p.day.slice(5), value: p.saved_tokens, display: tnum(p.saved_tokens) + " saved", color: "#D4A64A", sub: tnum(p.sent_tokens) + " sent" })),
          format: tnum, tooltip: (row) => h("div", {}, h("b", {}, row.id + " · " + row.display), h("div", {}, row.sub)),
          emptyText: "Nothing counted yet.", ariaLabel: "Tokens saved per day" }));
  const kinds = Object.entries(range.kinds || {}).sort((a, b) => (b[1].saved_tokens || 0) - (a[1].saved_tokens || 0));
  const byKind = h("div", { class: "tk-card" }, h("h3", {}, "By kind"),
    !kinds.length ? h("p", { class: "tk-note" }, "Nothing counted yet. Savings show up as workers get code packets and chats use read_symbol or read_outline.")
      : barChart({ id: "tk-kinds", orientation: "horizontal", animateKey: String(tk.days),
          rows: kinds.map(([k, v]) => ({ id: k, label: (TK_KINDS[k] || [k])[0], value: v.saved_tokens || 0, display: tnum(v.saved_tokens) + " saved", color: (TK_KINDS[k] || [0, "#D4A64A"])[1], sub: `${v.uses} uses · ${tnum(v.sent_tokens)} sent of ${tnum(v.raw_tokens)}` })),
          format: tnum, tooltip: (row) => h("div", {}, h("b", {}, row.label + " · " + row.display), h("div", {}, row.sub)),
          ariaLabel: "Tokens saved by kind" }));
  box.replaceChildren(
    h("div", { class: "tk-head" }, h("div", {}, h("h2", {}, "Token savings"), h("p", {}, "Estimated as characters ÷ 4, versus reading the whole files.")), rangeBtns),
    tiles, h("div", { class: "tk-two" }, daily, byKind));
}

/* Paid by: who asked for cross-station work and whose account ran it (GET /api/usage/ledger). */
async function ledgerSection(slot) {
  slot.innerHTML = `<p class="us-note">Loading…</p>`;
  let d;
  try {
    const r = await fetch("/api/usage/ledger", { cache: "no-store" });
    d = await r.json();
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  } catch (e) { slot.innerHTML = `<p class="us-note">Couldn't load the ledger: ${esc(e.message || e)}</p>`; return; }
  const rows = (d.groups || []).slice().sort((a, b) => (b.tokens || 0) - (a.tokens || 0));
  const head = `<h2>Paid by</h2><p class="us-note">Who asked for each piece of work, and whose account ran it.</p>`;
  if (!rows.length) { slot.innerHTML = head + `<p class="us-note">No cross-station work recorded yet. Workers run for or on linked stations show here.</p>`; return; }
  const n = (v) => Number(v || 0).toLocaleString();
  slot.innerHTML = head + `<div class="lg-scroll"><table class="lg-table"><thead><tr><th scope="col">Requested by</th><th scope="col">Paid by</th><th scope="col">Provider</th><th scope="col" class="num">Workers</th><th scope="col" class="num">Tokens</th></tr></thead><tbody>${
    rows.map((g) => `<tr><td>${esc(g.requested_by || "—")}</td><td>${esc(g.paid_by || "—")}</td><td>${esc(g.provider || "—")}</td><td class="num">${n(g.count)}</td><td class="num">${n(g.tokens)}</td></tr>`).join("")
  }</tbody><tfoot><tr><th scope="row" colspan="3">Total</th><td class="num">${n(d.count)}</td><td class="num">${n(d.total_tokens)}</td></tr></tfoot></table></div>`;
}
const LG_CSS = `.lg-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.lg-table{width:100%;min-width:520px;border-collapse:collapse;font-size:14px}
.lg-table th,.lg-table td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--hair,#e3e3dd);white-space:nowrap}
.lg-table thead th{font-size:12px;font-weight:600;color:var(--muted)}
.lg-table .num{text-align:right;font-variant-numeric:tabular-nums}
.lg-table tfoot th,.lg-table tfoot td{font-weight:600;border-bottom:0}`;
if (!document.getElementById("lg-css")) document.head.append(Object.assign(document.createElement("style"), { id: "lg-css", textContent: LG_CSS }));

const TABS = [["limits", "Limits"], ["models", "Models"], ["stats", "Stats"], ["diagnostics", "Diagnostics"], ["budgets", "Budgets"], ["ledger", "Paid by"]];
function mount() {
  unmount();
  const [r, sub1] = (location.hash.slice(1) || "home").split("/");
  if (r !== ROUTE) return;
  const sub0 = sub1 === "tokens" ? "stats" : sub1;   // old #usage/tokens links land on Stats
  const sub = TABS.some(([k]) => k === sub0) ? sub0 : "limits";
  document.body.classList.add("on-usage");
  if (!document.getElementById("us-css")) document.head.append(Object.assign(document.createElement("style"), { id: "us-css", textContent: CSS }));
  wrap = document.createElement("div");
  wrap.className = "us-wrap";
  const body = {
    limits: `<section class="us-sec"><div class="slot"></div></section>`,
    stats: `<section class="us-sec"><div class="slot"></div></section>`,
    models: `<section class="us-sec"><div class="slot"></div></section>`,
    budgets: `<section class="us-sec"><div class="slot"></div></section>`,
    tokens: `<section class="us-sec"><div class="slot"></div></section>`,
    ledger: `<section class="us-sec"><div class="slot"></div></section>`,
    diagnostics: `<section class="us-sec"><div id="dg" aria-live="polite"></div></section>`,
  }[sub];
  wrap.innerHTML = `<nav class="us-tabs" aria-label="Usage sections">${TABS.map(([k, l]) =>
    `<a href="#usage/${k}"${k === sub ? ' aria-current="page"' : ""}>${l}</a>`).join("")}</nav>${body}`;
  document.getElementById("page").append(wrap);
  if (sub === "limits") usageSection(wrap.querySelector(".slot"));
  else if (sub === "stats") statsSection(wrap.querySelector(".slot"));
  else if (sub === "models") modelsSection(wrap.querySelector(".slot"));
  else if (sub === "budgets") budgetsSection(wrap.querySelector(".slot"));
  else if (sub === "tokens") tokensSection(wrap.querySelector(".slot"));
  else if (sub === "ledger") ledgerSection(wrap.querySelector(".slot"));
  else {
    diagEl = wrap.querySelector("#dg");
    diagEl.addEventListener("click", onDiagClick);
    paintDiag();
    loadDiag();
    if (dg.digest == null) loadDigest();
    schedule();
  }
}
document.addEventListener("page:render", mount);
mount();
