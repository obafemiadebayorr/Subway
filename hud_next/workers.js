/* Workers page: Workers (each worker is a train ticket, from /api/status) · Permissions · Leases · Ledger.
   Leases and Ledger render the HUD's own views (hud/views.js) inside a shadow root, like the Usage tabs. */
import { h, api } from "/hud/lib.js";
import { shadowHost } from "./usage.js";

const ROUTE = "workers";
const TABS = [["workers", "Workers"], ["permissions", "Permissions"], ["leases", "Leases"], ["ledger", "Ledger"]];
const CSS = `
.wk-borrow{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.wk-chip{font-size:11px;padding:2px 8px;border-radius:99px;background:var(--ground);color:var(--muted);max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.us-wrap{display:flex;flex-direction:column;gap:16px;margin-top:16px;min-width:0}
.us-tabs{display:flex;gap:4px;padding:4px;border:1px solid var(--hair);border-radius:var(--radius);background:var(--surface);width:max-content;max-width:100%;overflow-x:auto;position:sticky;top:8px;z-index:3}
.us-tabs a{padding:7px 14px;border-radius:8px;color:var(--muted);text-decoration:none;font-size:13px;font-weight:500;white-space:nowrap}
.us-tabs a:hover{color:var(--ink)}
.us-tabs a[aria-current="page"]{background:var(--sky-chip-hi);color:var(--sky-ink)}
.us-sec{min-width:0}
.wq-empty{padding:28px;border:1px dashed var(--hair);border-radius:var(--radius);color:var(--muted);text-align:center}
.wq-card{background:var(--surface);border-radius:var(--radius);box-shadow:0 0 0 1px var(--hair),0 1px 2px rgba(17,17,17,.03)}
.wq-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.wq-stat{padding:14px 16px;display:flex;flex-direction:column;gap:2px;min-width:0}
.wq-stat b{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
.wq-stat span{font-size:12.5px;color:var(--muted)}
.wq-bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.wq-seg{display:flex;gap:4px;padding:4px;border-radius:var(--radius);background:var(--surface);box-shadow:0 0 0 1px var(--hair);overflow-x:auto;max-width:100%}
.wq-seg button{border:0;background:none;padding:6px 12px;border-radius:8px;font:inherit;font-size:13px;color:var(--muted);cursor:pointer;white-space:nowrap}
.wq-seg button[aria-pressed="true"]{background:var(--sky-chip-hi);color:var(--sky-ink)}
.wq-seg .n{margin-left:6px;font-size:11.5px;opacity:.75}
.wq-q{flex:1;min-width:160px;padding:8px 12px;border:0;border-radius:var(--radius);background:var(--surface);box-shadow:0 0 0 1px var(--hair);font:inherit;font-size:13px;color:var(--ink)}
.wq-list{display:grid;grid-template-columns:minmax(0,1fr);gap:16px}
.wq-more{font-size:12.5px;color:var(--muted);text-align:center;padding:6px;grid-column:1/-1}
.wq-perms{display:flex;flex-direction:column;gap:10px}
.wq-perm{padding:14px 16px;display:flex;flex-direction:column;gap:10px;border-left:3px solid #A87A1F}
.wq-perm-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
.wq-perm-title{font-size:14px;font-weight:600;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.wq-perm-detail{margin:0;padding:8px 10px;border-radius:8px;background:var(--ground);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere}
.wq-perm-meta{font-size:12.5px;color:var(--muted);display:flex;flex-wrap:wrap;gap:8px}

/* ---- the ticket ---- */
.wq-t{--c:#3D5247;position:relative;display:flex;flex-direction:column;background:var(--surface);border-radius:var(--radius);box-shadow:0 0 0 1px var(--hair),0 1px 2px rgba(17,17,17,.03);overflow:hidden;min-width:0}
.wq-t.run{--c:#2D6A88}.wq-t.wait{--c:#A87A1F}.wq-t.fail{--c:#A5432E}
.wq-band{display:flex;align-items:center;gap:10px;padding:9px 16px;background:var(--c);color:#fff;min-width:0}
.wq-band svg{flex-shrink:0}
.wq-band b{font-size:11.5px;letter-spacing:.16em;text-transform:uppercase;font-weight:700;white-space:nowrap}
.wq-band .st{margin-left:auto;font-size:12px;font-weight:600;display:flex;align-items:center;gap:6px;white-space:nowrap}
.wq-band .st i{width:7px;height:7px;border-radius:50%;background:#fff}
.wq-t.run .wq-band .st i{animation:wqp 1.6s ease-in-out infinite}
@keyframes wqp{50%{opacity:.3}}
@media (prefers-reduced-motion:reduce){.wq-t.run .wq-band .st i{animation:none}}
.wq-row{display:flex;flex-direction:column;min-width:0}
.wq-main{position:relative;flex:1;padding:16px;display:flex;flex-direction:column;gap:14px;min-width:0}
.wq-title{margin:0;font-size:18px;font-weight:650;line-height:1.25;overflow-wrap:anywhere}
.wq-main.stamped .wq-title{padding-right:112px}
.wq-line{font-size:13px;color:var(--muted);margin-top:2px}
.wq-route{display:grid;grid-template-columns:minmax(0,auto) minmax(84px,1fr) minmax(0,auto);align-items:end;gap:12px}
.wq-track{position:relative;height:30px}
.wq-track .rail{position:absolute;left:0;right:0;bottom:6px;border-top:2px dashed color-mix(in srgb,var(--c) 40%,transparent)}
.wq-track .stop{position:absolute;bottom:2px;width:10px;height:10px;border-radius:50%;background:var(--surface);box-shadow:inset 0 0 0 2.5px var(--c)}
.wq-track .stop.a{left:-5px}.wq-track .stop.b{right:-5px}
.wq-t.done .wq-track .stop.b{background:var(--c)}
.wq-train{position:absolute;bottom:7px;left:calc(var(--p,0) * (100% - 44px));width:44px;height:20px;color:var(--c);transition:left .9s cubic-bezier(.3,.7,.3,1)}
.wq-t.wait .wq-train{opacity:.55}
.wq-t.run .wq-train{animation:wqrock 1.2s ease-in-out infinite}
@keyframes wqrock{50%{transform:translateY(-1px)}}
@media (prefers-reduced-motion:reduce){.wq-train{transition:none}.wq-t.run .wq-train{animation:none}}
.wq-main,.wq-stub{background-image:var(--grain)}
.wq-t{--grain:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 0.2 0 0 0 0 0.18 0 0 0 0 0.12 0 0 0 .055 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}
.wq-punch{position:absolute;right:30px;top:52px;width:13px;height:13px;border-radius:50%;background:var(--ground);box-shadow:inset 0 1.5px 2px rgba(17,17,17,.22),0 0 0 1px var(--hair),0 1px 0 rgba(255,255,255,.7);pointer-events:none}
.wq-route .wq-f:last-child{text-align:right}
.wq-f{display:flex;flex-direction:column;gap:3px;min-width:0}
.wq-f span{font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);font-weight:600;white-space:nowrap}
.wq-f b{font-size:14px;font-weight:600;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wq-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 16px}
.wq-stamp{position:absolute;right:16px;top:14px;transform:rotate(-8deg);border:2px solid var(--c);color:var(--c);border-radius:6px;padding:3px 9px;font-size:11.5px;font-weight:800;letter-spacing:.16em;text-transform:uppercase;opacity:.8;pointer-events:none;white-space:nowrap}
.wq-perf{position:relative;flex-shrink:0;height:0;border-top:2px dashed var(--hair);margin:0 14px}
.wq-perf::before,.wq-perf::after{content:"";position:absolute;top:-9px;width:16px;height:16px;border-radius:50%;background:var(--ground);box-shadow:inset 0 0 0 1px var(--hair)}
.wq-perf::before{left:-23px}.wq-perf::after{right:-23px}
.wq-stub{padding:14px 16px 16px;display:flex;flex-direction:column;gap:12px;background:color-mix(in srgb,var(--c) 5%,var(--surface));min-width:0}
.wq-seat{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;min-width:0}
.wq-id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;font-weight:600;color:var(--c);overflow:hidden;text-overflow:ellipsis}
.wq-code{display:flex;align-items:stretch;gap:2px;height:30px;flex-shrink:0}
.wq-code i{display:block;background:var(--ink);opacity:.8;border-radius:.5px}
.wq-tags{display:flex;flex-wrap:wrap;gap:6px}
.wq-tag{font-size:11px;font-weight:600;padding:3px 8px;border-radius:99px;background:var(--surface);box-shadow:0 0 0 1px var(--hair);color:var(--muted);white-space:nowrap}
.wq-acts{display:flex;flex-wrap:wrap;gap:8px}
.wq-btn{border:0;border-radius:8px;padding:7px 12px;font:inherit;font-size:13px;font-weight:500;cursor:pointer;background:var(--surface);box-shadow:0 0 0 1px var(--hair);color:var(--ink)}
.wq-btn.primary{background:var(--c);box-shadow:none;color:#fff}
.wq-btn.danger{color:#9C3F2B}
.wq-btn:disabled{opacity:.55;cursor:default}
.wq-btn:focus-visible{outline:2px solid var(--c);outline-offset:2px}
.wq-undo-wrap{position:relative;display:inline-flex;vertical-align:middle}
.wq-btn.wq-undo{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:6px;overflow:hidden;white-space:nowrap;user-select:none;transition:background-color .15s ease-out,color .15s ease-out}
.wq-btn.wq-undo .wq-uface{display:inline-flex;align-items:center;gap:6px}
.wq-btn.wq-undo.entering .wq-uface{animation:wq-undo-in .22s cubic-bezier(0.23,1,0.32,1) both}
.wq-btn.wq-undo .wq-umuted{color:var(--muted)}
.wq-btn.wq-undo .wq-usep{width:1px;height:14px;flex:none;background:var(--hair)}
.wq-btn.wq-undo .wq-ucount{position:relative;display:inline-flex;justify-content:center;align-items:center;min-width:1ch;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;font-variant-numeric:tabular-nums}
.wq-btn.wq-undo .wq-ucount>span{display:block}
.wq-btn.wq-undo .wq-ucount>span.wq-utick{animation:wq-undo-tick .2s cubic-bezier(0.23,1,0.32,1) both}
.wq-btn.wq-undo .wq-ukbd{display:none;padding:1px 4px;border:1px solid var(--hair);border-radius:4px;color:var(--muted);font-size:11px;line-height:1.2}
@media (min-width:640px){.wq-btn.wq-undo .wq-ukbd{display:inline-block}}
.wq-undo-stroke{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;overflow:visible}
.wq-undo-stroke path{fill:none;stroke:var(--ink);stroke-width:1.5;stroke-linecap:round;transition:opacity .15s ease-out}
@keyframes wq-undo-in{from{opacity:0;transform:translateY(6px);filter:blur(2px)}to{opacity:1;transform:translateY(0);filter:blur(0)}}
@keyframes wq-undo-tick{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
@media (prefers-reduced-motion: reduce){
  .wq-btn.wq-undo,.wq-undo-stroke path{transition:none}
  .wq-btn.wq-undo.entering .wq-uface,.wq-btn.wq-undo .wq-ucount>span.wq-utick{animation:none}
}
.wq-foot{padding:0 16px 16px;display:flex;flex-direction:column;gap:10px;min-width:0}
.wq-report{margin:0;font-size:13px;line-height:1.5;white-space:pre-wrap;overflow-wrap:anywhere}
.wq-log{margin:0;max-height:320px;overflow:auto;padding:10px 12px;border-radius:8px;background:var(--ground);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere}
@media (min-width:640px){
  .wq-stats{grid-template-columns:repeat(4,minmax(0,1fr))}
  .wq-grid{grid-template-columns:repeat(auto-fit,minmax(92px,1fr))}
}
@media (min-width:900px){
  .wq-row{flex-direction:row}
  .wq-perf{height:auto;border-top:0;border-left:2px dashed var(--hair);margin:14px 0}
  .wq-perf::before{top:-23px;left:-9px}
  .wq-perf::after{top:auto;bottom:-23px;left:-9px;right:auto}
  .wq-stub{width:240px}
}
@media (min-width:1500px){.wq-list{grid-template-columns:repeat(2,minmax(0,1fr))}}
`;
let wrap = null;

async function status() {
  const r = await fetch("/api/status?view=next", { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function hudTab(slot, kind) {
  const { host, root } = shadowHost("us-hud");
  slot.replaceChildren(host);
  const box = h("div", {}, h("div", { class: "empty" }, "Loading…"));
  root.append(box);
  const paint = async () => {
    try {
      const [views, d] = await Promise.all([import("/hud/views.js"), status()]);
      let out;
      if (kind === "leases") out = views.leasesPage(d);
      else {
        const r = await fetch(`/api/ledger?name=${encodeURIComponent(views.ledger.track)}`, { cache: "no-store" });
        views.ledger.entries = r.ok ? ((await r.json()).entries || []) : [];
        out = views.ledgerPage(d, (name) => { views.ledger.track = name; views.ledger.entries = []; paint(); });
      }
      if (!wrap || !host.isConnected) return;
      box.replaceChildren(...(Array.isArray(out) ? out : [out]).filter(Boolean));
    } catch (e) {
      box.replaceChildren(h("div", { class: "empty" }, `Couldn't load ${kind}: ${e.message || e}`));
    }
  };
  paint();
}

/* ---------- Workers & Permissions ---------- */
const WK = { live: [], hist: [], allHist: [], lastTotal: -1, limit: 100, perms: [], total: 0, filter: "live", q: "", open: new Set(), logs: {}, logStreams: {}, busy: {}, permBusy: {}, undo: {}, err: "", timer: 0, loaded: false, chats: new Map(), chatsAt: 0 };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const ms = (v) => (typeof v === "number" ? (v > 1e12 ? v : v * 1000) : Date.parse(v || "") || 0);
const dur = (n) => { const s = Math.max(0, Math.round(n / 1000)); return s < 60 ? s + "s" : s < 3600 ? Math.floor(s / 60) + "m " + (s % 60) + "s" : Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m"; };
const clock = (t) => { if (!t) return "–"; const d = new Date(t); const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); return d.toDateString() === new Date().toDateString() ? hm : d.toLocaleDateString([], { day: "numeric", month: "short" }) + " " + hm; };
const tok = (n) => { n = Number(n || 0); return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n); };
const say = (m, k = "info") => { try { (window.toast?.[k] || window.toast || console.log)(m); } catch (_) {} };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-/i;
const chatName = (id) => { if (!id) return ""; const n = WK.chats.get(id); return n || (UUID.test(id) ? "Chat " + id.slice(0, 8) : id); };
function kind(w) {
  const s = String(w.status || "");
  if (/^(pending|awaiting|review|held|queued)/i.test(s)) return "wait";
  if (/^(running|starting|launching)/i.test(s)) return "run";
  return /^(failed|error|stopped|killed|rejected|timeout|cancel|budget)/i.test(s) || /budget_stopped|over_budget/i.test(s) ? "fail" : "done";
}
const FILTERS = [
  ["live", "Live", (w, k) => !w.archived && (k === "run" || k === "wait")],
  ["done", "Finished", (w, k) => !w.archived && k === "done"],
  ["fail", "Failed", (w, k) => !w.archived && k === "fail"],
  ["archived", "Archived", (w) => !!w.archived],
  ["all", "All", () => true],
];
const LOCO = `<svg class="wq-train" viewBox="0 0 44 20" fill="var(--surface)" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true"><path d="M3 4h25c5 0 10 3.5 12.5 9.5V15H3z"/><path d="M3 11.5h36.5" fill="none"/><rect x="6" y="6.5" width="5" height="3.2" rx=".8"/><rect x="13" y="6.5" width="5" height="3.2" rx=".8"/><rect x="20" y="6.5" width="5" height="3.2" rx=".8"/><path d="M29 6.5h1.2c2.6 0 4.6 1.4 6 3.2H29z"/><circle cx="9" cy="16.8" r="2"/><circle cx="17" cy="16.8" r="2"/><circle cx="31" cy="16.8" r="2"/><path d="M1 15h2M40.5 15h2.5" fill="none"/></svg>`;
const TRAIN = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="3" width="14" height="14" rx="3"/><path d="M5 11h14M9 21l-2-4M15 21l2-4"/><circle cx="9" cy="14" r=".6" fill="currentColor"/><circle cx="15" cy="14" r=".6" fill="currentColor"/></svg>`;

async function loadChats() {
  if (Date.now() - WK.chatsAt < 60000) return;
  WK.chatsAt = Date.now();
  try {
    const d = await (await fetch("/api/chats?limit=200", { cache: "no-store" })).json();
    for (const c of (Array.isArray(d) ? d : d.chats || [])) {
      const name = (c.aliases && c.aliases[0] && "@" + String(c.aliases[0]).replace(/^@/, "")) || c.tab_name || c.title;
      if (c.id && name) WK.chats.set(c.id, String(name).slice(0, 48));
    }
  } catch (_) {}
}
function updateTabsNav() {
  if (!wrap) return;
  const a = wrap.querySelector('.us-tabs a[href="#workers/permissions"]');
  if (a) a.textContent = WK.perms.length ? `Permissions (${WK.perms.length})` : "Permissions";
}
async function loadAllHistory() {
  try {
    const r = await fetch("/api/history?archived=include&limit=5000&view=next", { cache: "no-store" });
    if (r.ok) {
      const d = await r.json();
      WK.allHist = d.entries || [];
    }
  } catch (_) {}
}
async function loadWorkers(forceHist = false) {
  try {
    const [d] = await Promise.all([status(), loadChats()]);
    WK.live = d.workers || []; WK.hist = d.history || []; WK.perms = d.permissions || d.pending_permissions || []; WK.total = d.total_history || 0; WK.err = "";
    if (forceHist || WK.total !== WK.lastTotal || !WK.allHist.length) {
      WK.lastTotal = WK.total;
      loadAllHistory().then(() => { if (WK.slot?.isConnected) (WK.repaint || paintWorkers)(WK.slot); });
    }
  } catch (e) { WK.err = e.message || String(e); }
  WK.loaded = true;
  updateTabsNav();
  await Promise.all([...WK.open].map(loadLog));
  [...WK.open].forEach((id) => openLogStream(id, WK.slot));
}
async function loadLog(id) {
  try { const r = await fetch("/api/worker_log?id=" + encodeURIComponent(id), { cache: "no-store" }); WK.logs[id] = r.ok ? (await r.json()).log || "" : r.status === 404 ? "No log yet." : `Couldn't load the log (HTTP ${r.status}).`; }
  catch (e) { WK.logs[id] = "Couldn't load the log: " + (e.message || e); }
}
const WK_TERMINAL = /^(completed|failed|stopped|rejected|cancelled|canceled|error)$/i;
function closeLogStream(id) {
  const es = WK.logStreams[id];
  if (!es) return;
  delete WK.logStreams[id];
  try { es.close(); } catch (_) {}
}
function closeAllLogStreams() { for (const id of Object.keys(WK.logStreams)) closeLogStream(id); }
function openLogStream(id, slot) {
  if (typeof EventSource === "undefined") { loadLog(id).then(() => { const host = WK.slot || slot; if (host?.isConnected && WK.open.has(id)) paintWorkers(host); }); return; }
  if (WK.logStreams[id] || !WK.open.has(id)) return;
  const w = rows().find((x) => x && x.id === id);
  const status = String(w?.status || "");
  if (w && WK_TERMINAL.test(status) && WK.logs[id] !== undefined) return; // terminal + already fetched: nothing new will arrive
  const es = new EventSource("/api/worker_log/stream?id=" + encodeURIComponent(id));
  WK.logStreams[id] = es;
  es.addEventListener("log", (ev) => {
    let d = {}; try { d = JSON.parse(ev.data || "{}"); } catch (_) {}
    WK.logs[id] = d.log || "";
    const host = WK.slot || slot;
    const pre = host?.isConnected ? host.querySelector(`pre[data-log="${CSS.escape(id)}"]`) : null;
    if (pre) {
      // Update the rendered log in place: a full paintWorkers() rebuild per chunk would be far too heavy.
      const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 8;
      pre.textContent = WK.logs[id];
      if (atBottom) pre.scrollTop = pre.scrollHeight;
    } else if (host?.isConnected) paintWorkers(host);
  });
  es.addEventListener("done", () => closeLogStream(id));
  es.addEventListener("error", () => { if (es.readyState === 2) closeLogStream(id); });
}
function rows() {
  const seen = new Set(), out = [];
  for (const w of [...WK.live, ...WK.hist, ...WK.allHist]) if (w && w.id && !seen.has(w.id)) { seen.add(w.id); out.push(w); }
  return out;
}
function permCard(p) {
  const id = p.id || p.request_id || "";
  const busy = !!WK.permBusy[id];
  const pKind = p.kind || p.tool || p.type || "permission";
  const req = p.role || p.requester || p.from || p.worker_id || "Requester";
  const worker = p.worker_id && p.worker_id !== req ? p.worker_id : "";
  const prov = p.provider ? String(p.provider).toUpperCase() : "";
  const msg = p.detail ?? p.message ?? p.reason ?? p.command ?? "";
  const extraReason = p.reason && p.reason !== msg ? p.reason : "";
  const meta = [req, worker, prov, p.target || p.to ? `→ ${p.target || p.to}` : ""].filter(Boolean).join(" · ");
  const btn = (dec, label, cls = "") => `<button type="button" class="wq-btn ${cls}" data-perm="${dec}" data-id="${esc(id)}"${busy ? " disabled" : ""}>${esc(label)}</button>`;
  return `<article class="wq-card wq-perm" data-perm-card="${esc(id)}" aria-label="${esc("Permission " + id + " " + pKind)}">
    <div class="wq-perm-head">
      <div class="wq-perm-title"><span class="wq-tag">${esc(pKind)}</span><span>${esc(meta)}</span></div>
      <span class="wq-id">${esc(id)}</span>
    </div>
    ${msg ? `<pre class="wq-perm-detail">${esc(typeof msg === "string" ? msg : JSON.stringify(msg, null, 2))}</pre>` : ""}
    ${extraReason ? `<div class="wq-perm-meta">${esc(extraReason)}</div>` : ""}
    <div class="wq-acts">
      ${btn("allow", "Allow", "primary")}
      ${btn("allow_always", "Always allow")}
      ${btn("deny", "Deny", "danger")}
    </div>
  </article>`;
}
async function answerPerm(slot, id, decision, repaint) {
  if (!id || WK.permBusy[id]) return;
  WK.permBusy[id] = true;
  repaint(slot);
  try {
    const res = await api("/api/answer_permission", { id, decision });
    if (res?.error) say(res.error, "error");
    else {
      WK.perms = WK.perms.filter((p) => (p.id || p.request_id) !== id);
      updateTabsNav();
      say(decision === "deny" ? "Permission denied" : "Permission allowed", "success");
    }
  } catch (e) {
    say(e.message || String(e), "error");
  }
  delete WK.permBusy[id];
  repaint(slot);
  loadWorkers().then(() => slot.isConnected && repaint(slot));
}

/* A deterministic barcode from the worker id: decoration only. */
function barcode(id) {
  let x = 0;
  for (const c of String(id)) x = (Math.imul(x, 31) + c.charCodeAt(0)) >>> 0;
  let out = "";
  for (let i = 0; i < 26; i++) { x = (Math.imul(x, 1103515245) + 12345) >>> 0; out += `<i style="width:${1 + ((x >>> 16) % 3)}px"></i>`; }
  return `<span class="wq-code" aria-hidden="true">${out}</span>`;
}

const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || "");
const UNDO_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11"/></svg>';
function undoRectPath(w, h, r = 8) {
  const s = 0.75, rad = Math.max(0, Math.min(r - s, (w - 2 * s) / 2, (h - 2 * s) / 2)), left = s + rad, right = Math.max(left, w - s - rad), top = s + rad, bot = Math.max(top, h - s - rad);
  return `M ${w / 2} ${s} H ${right} A ${rad} ${rad} 0 0 1 ${w - s} ${top} V ${bot} A ${rad} ${rad} 0 0 1 ${right} ${h - s} H ${left} A ${rad} ${rad} 0 0 1 ${s} ${bot} V ${top} A ${rad} ${rad} 0 0 1 ${left} ${s} Z`;
}
function undoPendingHtml(key, entry, idAttr) {
  return `<span class="wq-undo-wrap" data-undo-key="${esc(key)}"><button type="button" class="wq-btn wq-undo${entry.entering ? " entering" : ""}" data-phase="pending" data-wk="undo" data-key="${esc(key)}"${idAttr ? ` data-id="${esc(idAttr)}"` : ""} aria-label="${esc(entry.doneLabel)}. Undo, ${entry.seconds} second${entry.seconds === 1 ? "" : "s"} left"><span class="wq-uface">${UNDO_SVG}<span>${esc(entry.doneLabel)}</span><span class="wq-usep" aria-hidden="true"></span><span class="wq-umuted">Undo</span><span class="wq-ucount" aria-hidden="true"><span>${entry.seconds}</span></span><kbd class="wq-ukbd">${IS_MAC ? "⌘Z" : "Ctrl Z"}</kbd></span></button><svg class="wq-undo-stroke" aria-hidden="true" focusable="false"><path d=""></path></svg></span>`;
}
function syncUndoVisuals(slot) {
  slot.querySelectorAll(".wq-undo-wrap[data-undo-key]").forEach((wrapEl) => {
    const entry = WK.undo[wrapEl.dataset.undoKey];
    if (!entry) return;
    const btn = wrapEl.querySelector(".wq-btn.wq-undo"), path = wrapEl.querySelector(".wq-undo-stroke path");
    if (!btn || !path) return;
    btn.setAttribute("aria-label", `${entry.doneLabel}. Undo, ${entry.seconds} second${entry.seconds === 1 ? "" : "s"} left`);
    const count = btn.querySelector(".wq-ucount");
    if (count && count.textContent !== String(entry.seconds)) count.innerHTML = `<span class="wq-utick">${entry.seconds}</span>`;
    const w = btn.offsetWidth, ht = btn.offsetHeight;
    if (!(w > 0 && ht > 0)) return;
    if (path.dataset.w !== String(w) || path.dataset.h !== String(ht)) {
      path.setAttribute("d", undoRectPath(w, ht, 8));
      path.dataset.w = String(w); path.dataset.h = String(ht);
    }
    const len = path.getTotalLength() || 0;
    const rem = Math.max(0, 1 - entry.elapsed / entry.duration);
    path.style.opacity = "1";
    path.style.strokeDasharray = `${len * rem} ${len}`;
    path.style.strokeDashoffset = "0";
  });
}
function startUndo(slot, key, { doneLabel, onCommit }) {
  if (WK.undo[key]) return;
  const entry = { key, doneLabel, duration: 5000, elapsed: 0, lastAt: performance.now(), hovered: false, seconds: 5, entering: true, raf: 0, slot, onCommit };
  WK.undo[key] = entry;
  setTimeout(() => { entry.entering = false; }, 240);
  paintWorkers(slot);
  const tick = (now) => {
    if (WK.undo[key] !== entry) return;
    if (!slot.isConnected) { delete WK.undo[key]; return; }
    if (!(entry.hovered || document.hidden)) entry.elapsed += Math.max(0, now - entry.lastAt);
    entry.lastAt = now;
    const left = Math.max(0, entry.duration - entry.elapsed);
    entry.seconds = Math.ceil(left / 1000);
    syncUndoVisuals(slot);
    if (left <= 0) {
      delete WK.undo[key];
      paintWorkers(slot);
      Promise.resolve(onCommit()).catch(() => {});
      return;
    }
    entry.raf = requestAnimationFrame(tick);
  };
  entry.raf = requestAnimationFrame(tick);
}
function cancelUndo(key) {
  const entry = WK.undo[key];
  if (!entry) return;
  if (entry.raf) cancelAnimationFrame(entry.raf);
  delete WK.undo[key];
  if (entry.slot?.isConnected) paintWorkers(entry.slot);
}

function ticket(w) {
  const k = kind(w), open = WK.open.has(w.id), busy = !!WK.busy[w.id];
  const start = ms(w.started_at || w.created_at), end = ms(w.completed_at);
  const journey = k === "run" && start ? dur(Date.now() - start) : end && start ? dur(end - start) : "–";
  const fare = w.tokens ? tok(w.tokens) : "–";
  const budget = w.cap ? (w.tokens ? Math.round((w.tokens / w.cap) * 100) + "% of " + tok(w.cap) : "Cap " + tok(w.cap)) : "No cap";
  const from = w.requested_by && !UUID.test(w.requested_by) ? "@" + w.requested_by : chatName(w.launched_by || w.requested_by) || "This station";
  const to = w.track ? "#" + w.track : "–";
  const line = [w.provider, w.model].filter(Boolean).join(" → ") + (w.effort ? ` · effort ${w.effort}` : "");
  const overBudget = /budget/i.test(String(w.status || ""));
  const stamp = k === "fail" ? (overBudget ? "Over budget" : String(w.status || "Failed").replace(/_/g, " ")) : k === "done" ? (w.merged ? "Merged" : w.branch_name ? "Not merged" : "Completed") : "";
  const status = String(w.status || "").replace(/_/g, " ");
  const prog = k === "wait" ? 0 : k === "done" ? 1 : w.cap && w.tokens ? Math.min(0.96, w.tokens / w.cap)
    : k === "run" && start ? Math.min(0.94, 1 - Math.exp(-(Date.now() - start) / 600000)) : 0.5;
  const tags = [w.archived && "Archived", w.isolate && "Isolated", w.mode === "god" && "Prompts off", ["A", "B", "C"].includes(String(w.billing_mode || "")) && "Mode " + w.billing_mode,
    w.swarm_id && "Swarm", w.exit_code != null && w.exit_code !== 0 && "Exit " + w.exit_code, w.tool_calls && w.tool_calls + " tool calls"].filter(Boolean);
  const b = (act, label, cls = "") => `<button type="button" class="wq-btn ${cls}" data-wk="${act}" data-id="${esc(w.id)}"${busy ? " disabled" : ""}>${esc(label)}</button>`;
  const acts = (k === "run" ? b("stop", "Stop", "danger") : k === "wait" ? b("approve", "Approve", "primary") + b("reject", "Reject", "danger") : (WK.undo[w.id] ? undoPendingHtml(w.id, WK.undo[w.id], w.id) : w.archived ? b("unarchive", "Unarchive") : b("archive", "Archive")))
    + ((w.branch_name || w.merged || w.isolate) ? b("diff", "View diff") : "")
    + `<button type="button" class="wq-btn" data-wk="toggle" data-id="${esc(w.id)}" aria-expanded="${open}">${open ? "Hide log" : "Show log"}</button>`;
  const f = (label, value, title = "") => `<div class="wq-f"><span>${label}</span><b${title ? ` title="${esc(title)}"` : ""}>${esc(value)}</b></div>`;
  return `<article class="wq-t ${k}" aria-label="${esc((w.role || "Worker") + ", " + status)}">
    <div class="wq-band">${TRAIN}<b>Worker ticket</b><span class="st"><i aria-hidden="true"></i>${esc(status)}</span></div>
    <div class="wq-row">
      <div class="wq-main${stamp ? " stamped" : ""}">
        ${stamp ? `<span class="wq-stamp" aria-hidden="true">${esc(stamp)}</span><span class="wq-punch" aria-hidden="true"></span>` : ""}
        <div><h3 class="wq-title">${esc(w.role || "Worker")}</h3><div class="wq-line">${esc(line)}</div>${(w.skills || []).length || (w.mcp || []).length ? `<div class="wk-borrow" aria-label="Borrowed">${(w.skills || []).map((n) => `<span class="wk-chip" title="Skill">✦ ${esc(n)}</span>`).join("")}${(w.mcp || []).map((n) => `<span class="wk-chip" title="MCP server">⚡ ${esc(n)}</span>`).join("")}</div>` : ""}</div>
        <div class="wq-route">${f("From", from, w.launched_by || "")}<span class="wq-track" style="--p:${prog.toFixed(3)}" aria-hidden="true"><span class="rail"></span><span class="stop a"></span><span class="stop b"></span>${LOCO}</span>${f("To", to)}</div>
        <div class="wq-grid">${f("Departed", clock(start))}${f(k === "run" ? "Arrives" : "Arrived", k === "run" ? "En route" : k === "wait" ? "Not boarded" : clock(end))}${f("Journey", journey)}${f("Fare", fare)}${f("Budget", budget)}</div>
      </div>
      <div class="wq-perf" aria-hidden="true"></div>
      <div class="wq-stub">
        <div class="wq-seat"><div class="wq-f"><span>Seat</span><span class="wq-id">${esc(w.id)}</span></div>${barcode(w.id)}</div>
        ${tags.length ? `<div class="wq-tags">${tags.map((t) => `<span class="wq-tag">${esc(t)}</span>`).join("")}</div>` : ""}
        <div class="wq-acts">${acts}${b("copy", "Copy id")}</div>
      </div>
    </div>
    ${open ? `<div class="wq-foot">
      ${w.reason ? `<p class="wq-report"><b>Reason:</b> ${esc(w.reason)}</p>` : ""}
      ${w.report ? `<p class="wq-report">${esc(w.report)}</p>` : ""}
      <pre class="wq-log" data-log="${esc(w.id)}" aria-label="Log tail">${esc(WK.logs[w.id] ?? "Loading log…")}</pre></div>` : ""}
  </article>`;
}

function paintWorkers(slot) {
  const all = rows().map((w) => [w, kind(w)]);
  const today = new Date().toDateString();
  const isToday = (w) => new Date(ms(w.completed_at)).toDateString() === today;
  const n = { run: 0, wait: 0, done: 0, fail: 0 };
  all.forEach(([w, k]) => { if (!w.archived && (k === "run" || k === "wait") || isToday(w)) n[k]++; });
  const q = WK.q.trim().toLowerCase();
  const [, , pick] = FILTERS.find(([key]) => key === WK.filter) || FILTERS[0];
  const shown = all.filter(([w, k]) => pick(w, k) && (!q || [w.role, w.id, w.provider, w.model, w.track, chatName(w.launched_by)].some((v) => String(v || "").toLowerCase().includes(q))));
  slot.querySelector(".wq-stats").innerHTML = [["Running", n.run], ["Waiting on you", n.wait + WK.perms.length], ["Finished today", n.done], ["Failed today", n.fail]]
    .map(([l, v]) => `<div class="wq-card wq-stat"><b>${v}</b><span>${l}</span></div>`).join("");
  const pSlot = slot.querySelector(".wq-perms");
  if (pSlot) pSlot.innerHTML = WK.perms.length ? WK.perms.map(permCard).join("") : "";
  slot.querySelector(".wq-seg").innerHTML = FILTERS.map(([key, l, fn]) => `<button type="button" data-wk="filter" data-f="${key}" aria-pressed="${WK.filter === key}">${l}<span class="n">${all.filter(([w, k]) => fn(w, k)).length}</span></button>`).join("");
  const archHost = slot.querySelector(".wq-undo-host");
  const canArchAll = (WK.filter === "done" || WK.filter === "fail") && all.some(([w, k]) => !w.archived && (k === "done" || k === "fail"));
  if (archHost) {
    if (WK.undo.__all__) { archHost.innerHTML = undoPendingHtml("__all__", WK.undo.__all__, ""); archHost.hidden = false; }
    else if (canArchAll) { archHost.innerHTML = '<button type="button" class="wq-btn" data-wk="archive_all">Archive finished</button>'; archHost.hidden = false; }
    else { archHost.innerHTML = ""; archHost.hidden = true; }
  }
  const list = slot.querySelector(".wq-list");
  const keep = {};
  list.querySelectorAll("[data-log]").forEach((p) => { keep[p.dataset.log] = p.scrollHeight - p.scrollTop - p.clientHeight < 8 ? -1 : p.scrollTop; });
  if (WK.err && !all.length) list.innerHTML = `<div class="wq-empty">Couldn't load workers: ${esc(WK.err)}</div>`;
  else if (!WK.loaded) list.innerHTML = `<div class="wq-empty">Loading workers…</div>`;
  else if (!shown.length) list.innerHTML = `<div class="wq-empty">${q ? "No workers match that search." : WK.filter === "live" ? "No workers running. Spin one from a chat and its ticket shows up here." : WK.filter === "archived" ? "No archived workers." : "Nothing here yet."}</div>`;
  else {
    const visible = shown.slice(0, WK.limit);
    const more = shown.length > visible.length
      ? `<div class="wq-more" style="display:flex;justify-content:center;align-items:center;gap:10px;flex-wrap:wrap"><span>Showing ${visible.length} of ${shown.length} workers</span><button type="button" class="wq-btn" data-wk="more">Load more (+100)</button><button type="button" class="wq-btn" data-wk="all_rows">Show all (${shown.length})</button></div>`
      : "";
    list.innerHTML = visible.map(([w]) => ticket(w)).join("") + more;
  }
  list.querySelectorAll("[data-log]").forEach((p) => { const v = keep[p.dataset.log]; p.scrollTop = v === undefined || v === -1 ? p.scrollHeight : v; });
  [...WK.open].forEach((id) => openLogStream(id, slot));
  syncUndoVisuals(slot);
}

async function act(slot, a, id) {
  if (a === "more") { WK.limit += 100; paintWorkers(slot); return; }
  if (a === "all_rows") { WK.limit = 10000; paintWorkers(slot); return; }
  if (a === "archive_all") {
    startUndo(slot, "__all__", {
      doneLabel: "Archived",
      onCommit: async () => {
        try {
          const res = await api("/api/clear_history", {});
          if (res?.error) say(res.error, "error");
          else {
            rows().forEach((w) => { const k = kind(w); if (k === "done" || k === "fail") w.archived = true; });
            say("Archived finished workers", "success");
          }
        } catch (e) { say(e.message || String(e), "error"); }
        paintWorkers(slot);
        loadWorkers(true).then(() => paintWorkers(slot));
      },
    });
    return;
  }
  const matches = [...WK.live, ...WK.hist, ...WK.allHist].filter((x) => x && x.id === id);
  const w = matches[0] || {};
  if (a === "toggle") {
    if (WK.open.has(id)) { WK.open.delete(id); closeLogStream(id); }
    else { WK.open.add(id); loadLog(id).then(() => paintWorkers(slot)); openLogStream(id, slot); }
    paintWorkers(slot); return;
  }
  if (a === "diff") { location.hash = "#landing/" + encodeURIComponent(id); return; }
  if (a === "copy") { try { await navigator.clipboard.writeText(id); say("Worker id copied", "success"); } catch (_) {} return; }
  if (a === "archive" || a === "unarchive") {
    startUndo(slot, id, { doneLabel: a === "archive" ? "Archived" : "Restored", onCommit: () => commitWorkerAction(slot, a, id) });
    return;
  }
  const call = { stop: ["/api/stop_worker", `Stop ${w.role || id}?`, "Stopped"], approve: ["/api/approve_worker", "", "Approved"], reject: ["/api/reject_worker", `Reject ${w.role || id}?`, "Rejected"] }[a];
  if (!call || (call[1] && !confirm(call[1]))) return;
  // Optimistic: the ticket and toast change on click; the server result reconciles (or reverts) after.
  const prev = { status: w.status, archived: w.archived };
  const next = { approve: { status: "running" }, stop: { status: "stopped" }, reject: { status: "rejected" }, archive: { archived: true }, unarchive: { archived: false } }[a];
  matches.forEach((m) => Object.assign(m, next)); WK.busy[id] = true; paintWorkers(slot); say(call[2], "success");
  try { const res = await api(call[0], { id, worker_id: id }); if (res?.error) { matches.forEach((m) => Object.assign(m, prev)); say(res.error, "error"); } }
  catch (e) { matches.forEach((m) => Object.assign(m, prev)); say(e.message || String(e), "error"); }
  delete WK.busy[id];
  paintWorkers(slot); loadWorkers(a === "archive" || a === "unarchive").then(() => paintWorkers(slot));
}

async function commitWorkerAction(slot, a, id) {
  const matches = [...WK.live, ...WK.hist, ...WK.allHist].filter((x) => x && x.id === id);
  const w = matches[0] || {};
  const call = { archive: ["/api/worker_archive", "Archived"], unarchive: ["/api/worker_unarchive", "Restored"] }[a];
  if (!call) return;
  // Optimistic: the ticket and toast change on click; the server result reconciles (or reverts) after.
  const prev = { archived: w.archived };
  const next = { archive: { archived: true }, unarchive: { archived: false } }[a];
  matches.forEach((m) => Object.assign(m, next)); WK.busy[id] = true; paintWorkers(slot); say(call[1], "success");
  try { const res = await api(call[0], { id, worker_id: id }); if (res?.error) { matches.forEach((m) => Object.assign(m, prev)); say(res.error, "error"); } }
  catch (e) { matches.forEach((m) => Object.assign(m, prev)); say(e.message || String(e), "error"); }
  delete WK.busy[id];
  paintWorkers(slot); loadWorkers(true).then(() => paintWorkers(slot));
}

function workersTab(slot) {
  slot.innerHTML = `<div style="display:flex;flex-direction:column;gap:12px"><div class="wq-stats"></div>
    <div class="wq-perms" aria-label="Pending permissions"></div>
    <div class="wq-bar"><div class="wq-seg" role="group" aria-label="Filter workers"></div><input class="wq-q" type="search" placeholder="Search role, id, model, track or chat" aria-label="Search workers" value="${esc(WK.q)}"><span class="wq-undo-host"></span></div>
    <div class="wq-list" aria-live="polite"></div></div>`;
  slot.addEventListener("click", (e) => {
    const pb = e.target.closest("[data-perm]");
    if (pb && !pb.disabled) { answerPerm(slot, pb.dataset.id, pb.dataset.perm, paintWorkers); return; }
    const b = e.target.closest("[data-wk]");
    if (!b || b.disabled) return;
    if (b.dataset.wk === "undo") { cancelUndo(b.dataset.key || b.dataset.id); return; }
    if (b.dataset.wk === "filter") { WK.filter = b.dataset.f; WK.limit = 100; paintWorkers(slot); return; }
    act(slot, b.dataset.wk, b.dataset.id);
  });
  slot.addEventListener("pointerover", (e) => {
    if (e.pointerType !== "mouse") return;
    const btn = e.target.closest(".wq-btn.wq-undo");
    const entry = btn && WK.undo[btn.dataset.key];
    if (entry) entry.hovered = true;
  });
  slot.addEventListener("pointerout", (e) => {
    if (e.pointerType !== "mouse") return;
    const btn = e.target.closest(".wq-btn.wq-undo");
    const entry = btn && WK.undo[btn.dataset.key];
    if (entry) entry.hovered = false;
  });
  if (!WK.onUndoKey) {
    WK.onUndoKey = (e) => {
      const sl = WK.slot;
      if (!sl?.isConnected) return;
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && String(e.key || "").toLowerCase() === "z") {
        const t = e.target;
        if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
        const keys = Object.keys(WK.undo);
        if (!keys.length) return;
        cancelUndo(keys[keys.length - 1]);
        e.preventDefault();
      }
    };
    window.addEventListener("keydown", WK.onUndoKey);
  }
  slot.querySelector(".wq-q").addEventListener("input", (e) => { WK.q = e.target.value; WK.limit = 100; paintWorkers(slot); });
  paintWorkers(slot);
  clearTimeout(WK.timer); if (wrap && slot.isConnected) loadWorkers().then(() => slot.isConnected && paintWorkers(slot));
  // Server push (push.js): reload the moment a worker changes; the 5s poll is only the fallback.
  WK.slot = slot;
  WK.repaint = paintWorkers;
  if (!WK.onPush) { WK.onPush = () => { const sl = WK.slot; if (wrap && sl?.isConnected) loadWorkers().then(() => sl.isConnected && (WK.repaint || paintWorkers)(sl)); }; window.addEventListener("subway:push", WK.onPush); }
}

function paintPermissions(slot) {
  const box = slot.querySelector(".wq-perms");
  if (!box) return;
  if (WK.err && !WK.perms.length) box.innerHTML = `<div class="wq-empty">Couldn't load permissions: ${esc(WK.err)}</div>`;
  else if (!WK.loaded) box.innerHTML = `<div class="wq-empty">Loading permissions…</div>`;
  else if (!WK.perms.length) box.innerHTML = `<div class="wq-empty">No pending permission requests.</div>`;
  else {
    const bulk = WK.perms.length > 1
      ? `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><span>${WK.perms.length} pending requests</span><button type="button" class="wq-btn danger" data-perm-all="deny"${WK.permBusy.__all ? " disabled" : ""}>Deny all (${WK.perms.length})</button></div>`
      : "";
    box.innerHTML = bulk + WK.perms.map(permCard).join("");
  }
}

async function denyAllPerms(slot) {
  if (WK.permBusy.__all || !WK.perms.length) return;
  WK.permBusy.__all = true;
  paintPermissions(slot);
  const todo = [...WK.perms];
  const failed = new Set();
  for (const p of todo) {
    const id = p.id || p.request_id || "";
    if (!id) continue;
    try {
      const res = await api("/api/answer_permission", { id, decision: "deny" });
      if (res?.error) failed.add(id);
    } catch (_) {
      failed.add(id);
    }
  }
  WK.perms = WK.perms.filter((p) => failed.has(p.id || p.request_id || ""));
  delete WK.permBusy.__all;
  updateTabsNav();
  say(failed.size ? `${failed.size} permission(s) could not be denied` : "All permissions denied", failed.size ? "error" : "success");
  paintPermissions(slot);
  loadWorkers().then(() => slot.isConnected && paintPermissions(slot));
}

function permissionsTab(slot) {
  slot.innerHTML = `<div class="wq-perms" aria-live="polite" aria-label="Pending permissions"></div>`;
  slot.addEventListener("click", (e) => {
    const allBtn = e.target.closest('[data-perm-all="deny"]');
    if (allBtn && !allBtn.disabled) { denyAllPerms(slot); return; }
    const pb = e.target.closest("[data-perm]");
    if (pb && !pb.disabled) answerPerm(slot, pb.dataset.id, pb.dataset.perm, paintPermissions);
  });
  paintPermissions(slot);
  clearTimeout(WK.timer); if (wrap && slot.isConnected) loadWorkers().then(() => slot.isConnected && paintPermissions(slot));
  WK.slot = slot;
  WK.repaint = paintPermissions;
  if (!WK.onPush) { WK.onPush = () => { const sl = WK.slot; if (wrap && sl?.isConnected) loadWorkers().then(() => sl.isConnected && (WK.repaint || paintPermissions)(sl)); }; window.addEventListener("subway:push", WK.onPush); }
}

function unmount() {
  document.body.classList.remove("on-workers");
  closeAllLogStreams();
  clearTimeout(WK.timer);
  wrap?.remove(); wrap = null;
}

function mount() {
  unmount();
  const [r, sub0] = (location.hash.slice(1) || "home").split("/");
  if (r !== ROUTE) return;
  const sub = TABS.some(([k]) => k === sub0) ? sub0 : "workers";
  document.body.classList.add("on-workers");
  if (!document.getElementById("wq-css")) document.head.append(Object.assign(document.createElement("style"), { id: "wq-css", textContent: CSS }));
  wrap = document.createElement("div");
  wrap.className = "us-wrap";
  wrap.innerHTML = `<nav class="us-tabs" aria-label="Workers sections">${TABS.map(([k, l]) =>
    `<a href="#workers/${k}"${k === sub ? ' aria-current="page"' : ""}>${k === "permissions" && WK.perms.length ? `Permissions (${WK.perms.length})` : l}</a>`).join("")}</nav><section class="us-sec"><div class="slot"></div></section>`;
  document.getElementById("page").append(wrap);
  const slot = wrap.querySelector(".slot");
  if (sub === "workers") workersTab(slot);
  else if (sub === "permissions") permissionsTab(slot);
  else hudTab(slot, sub);
}

document.addEventListener("page:render", mount);
mount();
