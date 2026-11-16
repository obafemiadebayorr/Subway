/* Collab page (real data from the HUD APIs): session bar, trains (engine + coaches),
   coordination (claims, live workers, decisions/handoffs) and the archived remote-worker history.
   Mobile first: base = phone, 768px = tablet, 1200px = desktop. */

const ROUTE = "collab";
import { api } from "/hud/lib.js";
/* Prototype toast: lib.js notifications need the HUD stylesheet, which the prototype does not load. */
function toast(msg) {
  let box = document.getElementById("cb-toasts");
  if (!box) { box = document.createElement("div"); box.id = "cb-toasts"; box.setAttribute("role", "status"); box.setAttribute("aria-live", "polite");
    box.style.cssText = "position:fixed;left:50%;bottom:88px;transform:translateX(-50%);z-index:50;display:grid;gap:6px;max-width:min(92vw,420px);pointer-events:none";
    document.body.appendChild(box); }
  const t = document.createElement("div");
  t.textContent = String(msg || "");
  t.style.cssText = "background:var(--ink,#1a2b38);color:var(--surface,#fff);padding:10px 14px;border-radius:10px;font-size:13px;box-shadow:0 6px 20px rgba(0,0,0,.18)";
  box.appendChild(t); setTimeout(() => t.remove(), 3200);
}

const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------- live data (real APIs only; no mock data) ---------- */
const S = { loaded: false, session: null, usage: null, peers: [], allowance: {}, history: {}, models: {}, coord: null, error: "", remote: [], local: [] };
const ok = (r) => (r && r.status === "fulfilled" ? r.value : null);

async function loadCollab() {
  const [sess, usage, peers, models, coord, rw, st, pl] = await Promise.allSettled([
    api("/api/collab/session"),
    api("/api/collab/usage"),
    api("/api/peers"),
    api("/api/peers/models"),
    api("/api/coordination?track=main"),
    api("/api/remote_workers"),
    api("/api/status?view=next"),
    api("/api/pair/list"),
  ]);
  S.session = (ok(sess) || {}).session || null;
  S.usage = ok(usage);
  S.peers = (ok(peers) || {}).peers || [];
  S.grantSchema = (ok(peers) || {}).grant_schema || [];
  S.error = sess.status === "rejected" ? String(sess.reason?.message || sess.reason) : "";
  S.models = {};
  for (const st of (ok(models) || {}).stations || []) if (st && st.station) S.models[st.station] = st;
  S.coord = ok(coord);
  S.remote = (ok(rw) || {}).workers || [];
  /* Phones paired to this station ride as coaches on its own train. */
  S.phones = ((ok(pl) || {}).devices || []).filter((d) => !d.revoked && /ios|iphone|ipad|android|pixel|galaxy/i.test(`${d.platform || ""} ${d.name || ""}`));
  const wl = (ok(st) || {}).workers;
  S.local = (Array.isArray(wl) ? wl : Object.values(wl || {})).filter((w) => w && ((w.meta || {}).awaiting_local_initiator || w.awaiting_local_initiator || /^(pending|awaiting)/i.test(String(w.status || ""))));
  await Promise.all(S.peers.flatMap((p) => [
    api("/api/peers/allowance?peer_id=" + encodeURIComponent(p.peer_id)).then((a) => (S.allowance[p.peer_id] = a)).catch(() => {}),
    api("/api/peers/allowance/history?peer_id=" + encodeURIComponent(p.peer_id)).then((h) => (S.history[p.peer_id] = h.history || [])).catch(() => {}),
  ]));
  S.loaded = true;
}

async function post(path, body, done = "Done") {
  try { await api(path, body); toast(done); }
  catch (e) { toast(e.message || String(e)); }
  await loadCollab(); render();
}

const slugOf = (p) => p.alias || p.name || p.peer_id;
const ago = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - Number(ts || 0));
  if (!ts) return "";
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
};
const tok = (n) => { n = Number(n || 0); return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n); };
const kindIcon = (k) => (/phone|mobile/i.test(k || "") ? "phone" : /desk|tower|mini|pc/i.test(k || "") ? "desktop" : "laptop");
const GRANT_LABELS = { spawn: "workers", files: "files", claims: "claims", chats: "chats", land: "landing", mail: "mail" };

/* Members -> trains: an engine is a member with no `via`; its coaches ride `via` it. */
function trains() {
  const s = S.session;
  if (!s) return [];
  const mem = s.members || [];
  const usage = new Map(((S.usage || {}).members || []).map((m) => [m.device_id, m]));
  const engines = mem.filter((m) => !m.via);
  return engines.map((e) => ({
    id: e.device_id,
    person: e.person || e.name || "Station",
    me: !!e.self,
    host: e.role === "host" || e.device_id === s.host_device,
    master: e.device_id === s.master_device,
    card: e.station_card || {},
    usage: usage.get(e.device_id),
    engine: { name: e.name || e.device_id, kind: e.device_kind, state: e.online === false ? "offline" : "online" },
    coaches: mem.filter((m) => m.via === e.device_id).map((c) => ({
      id: c.device_id, name: c.name || c.device_id, kind: kindIcon(c.device_kind),
      state: c.online === false ? "offline" : "online", shared: true,
    })).concat(e.self ? (S.phones || []).map((p) => ({
      id: p.device_id, name: p.name || "Phone", kind: "phone", shared: false,
      state: Date.now() / 1000 - Number(p.last_seen || 0) < 180 ? "online" : "offline", note: "Phone · paired here",
    })) : []),
  }));
}

/* ---------- local UI state ---------- */
const ui = { tab: "people", pane: "claims" };

/* ---------- icons (inline, stroke) ---------- */
const P = {
  engine: '<rect x="3" y="6" width="18" height="11" rx="2"/><path d="M7 21h10M12 17v4"/>',
  laptop: '<rect x="4" y="5" width="16" height="11" rx="1.5"/><path d="M2 19h20"/>',
  phone: '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
  desktop: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v.01M14 20h.01M17 20h4v-3"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
  hand: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  note: '<path d="M4 5h16v11H8l-4 4z"/>',
  warn: '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17v.01"/>',
  chev: '<path d="m9 6 6 6-6 6"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  archive: '<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4"/>',
};
const ic = (n, s = 16) => `<svg class="cb-i" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[n]}</svg>`;
const dot = (state) => `<span class="cb-dot is-${esc(state)}" aria-hidden="true"></span>`;
const stateText = (s, last) => s === "online" ? "Online" : s === "idle" ? `Idle${last ? " · " + last : ""}` : `Offline${last ? " · last seen " + last : ""}`;

/* ---------- CSS ---------- */
const CSS = `
.cb{display:flex;flex-direction:column;gap:16px;margin-top:16px;min-width:0;color:var(--ink)}
.cb-i{flex-shrink:0}
.cb button{font:inherit}
.cb-btn{display:inline-flex;align-items:center;gap:6px;min-height:36px;padding:0 14px;border-radius:10px;border:1px solid var(--hair);background:var(--surface);color:var(--ink);font-size:13px;font-weight:500;cursor:pointer;white-space:nowrap}
.cb-btn:hover{border-color:var(--sky-shade)}
.cb-btn.primary{background:var(--ink);border-color:var(--ink);color:#fff}
.cb-btn.gold{background:var(--ochre);border-color:var(--ochre);color:#1d1606}
.cb-btn.ghost{border-color:transparent;background:transparent;color:var(--muted)}
.cb-btn.danger{color:#A23B2A}
.cb-btn:focus-visible,.cb-seg button:focus-visible,.cb-sw:focus-visible,.cb summary:focus-visible{outline:2px solid var(--ochre);outline-offset:2px}
.cb-card{background:var(--surface);border:1px solid var(--hair);border-radius:var(--radius);min-width:0}
.cb-h{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin:0 0 10px}
.cb-h h2{margin:0;font-size:15px;font-weight:600}
.cb-h p{margin:0;font-size:12px;color:var(--muted)}
.cb-muted{color:var(--muted)}
.cb-mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.cb-badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;background:var(--ground);color:var(--muted);white-space:nowrap}
.cb-badge.gold{background:rgba(212,166,74,.16);color:#8a6418}
.cb-badge.sky{background:var(--sky-chip);color:var(--sky-ink)}
.cb-dot{width:8px;height:8px;border-radius:50%;background:#B9C2BC;flex-shrink:0}
.cb-dot.is-online{background:#3E9C6B;box-shadow:0 0 0 3px rgba(62,156,107,.15)}
.cb-dot.is-idle{background:var(--ochre)}
.cb-dot.is-running{background:var(--sky);box-shadow:0 0 0 3px rgba(38,146,166,.15)}
.cb-dot.is-waiting{background:var(--ochre)}
.cb-dot.is-done{background:#B9C2BC}

/* preview switcher */
.cb-preview{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:12px;color:var(--muted)}
.cb-seg{display:inline-flex;gap:2px;padding:3px;border:1px solid var(--hair);border-radius:10px;background:var(--surface);max-width:100%;overflow-x:auto}
.cb-seg button{all:unset;cursor:pointer;padding:6px 12px;border-radius:7px;font-size:12.5px;color:var(--muted);white-space:nowrap;display:inline-flex;gap:6px;align-items:center}
.cb-seg button[aria-pressed="true"]{background:var(--sky-chip-hi);color:var(--sky-ink);font-weight:600}
.cb-seg .n{font-size:11px;padding:0 6px;border-radius:999px;background:var(--ground)}

/* session bar */
.cb-bar{position:sticky;top:8px;z-index:4;padding:12px 14px;display:flex;flex-direction:column;gap:12px;box-shadow:0 6px 18px -14px rgba(15,47,58,.45)}
.cb-bar-main{display:flex;align-items:center;gap:12px;min-width:0}
.cb-bar-ttl{min-width:0;flex:1}
.cb-bar-ttl b{display:block;font-size:15px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cb-bar-ttl span{font-size:12px;color:var(--muted)}
.cb-live{width:10px;height:10px;border-radius:50%;background:var(--ochre);box-shadow:0 0 0 4px rgba(212,166,74,.18);flex-shrink:0}
.cb-code{display:inline-flex;align-items:center;gap:8px;padding:6px 6px 6px 12px;border-radius:10px;background:var(--ground);font-family:ui-monospace,Menlo,monospace;font-size:14px;letter-spacing:.08em;font-weight:600}
.cb-code button{all:unset;cursor:pointer;display:grid;place-items:center;width:28px;height:28px;border-radius:7px;color:var(--muted)}
.cb-code button:hover{background:var(--surface);color:var(--ink)}
.cb-bar-acts{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.cb-bar-acts .sp{flex:1}
.cb-join{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.cb-join input{flex:1;min-width:140px;min-height:36px;border:1px solid var(--hair);border-radius:10px;padding:0 12px;font:600 14px ui-monospace,Menlo,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--ink);background:var(--surface)}
.cb-join input:focus{outline:2px solid var(--ochre);outline-offset:1px}
.cb-banner{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border-radius:10px;background:rgba(212,166,74,.12);color:#6d4f12;font-size:13px}
.cb-banner .grow{flex:1}
.cb-banner button{all:unset;cursor:pointer;color:inherit;opacity:.7}

/* trains */
.cb-map{display:none;padding:8px 12px 4px}
.cb-map svg{width:100%;height:auto;display:block}
.cb-map text{font:500 11px system-ui,-apple-system,sans-serif;fill:var(--muted)}
.cb-map .eng text.nm{fill:var(--ink);font-weight:600;font-size:12px}
.cb-trains{display:grid;gap:12px}
.cb-train{padding:14px}
.cb-train.me{border-color:rgba(212,166,74,.55)}
.cb-train-h{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.cb-av{width:32px;height:32px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:13px;background:var(--sky-chip);color:var(--sky-ink);flex-shrink:0}
.cb-train.me .cb-av{background:rgba(212,166,74,.2);color:#8a6418}
.cb-train-h b{font-size:14px}
.cb-train-h .grow{flex:1;min-width:0;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.cb-eng{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;background:var(--ground)}
.cb-eng .nm{font-weight:600;font-size:13.5px}
.cb-eng .grow{flex:1;min-width:0}
.cb-eng small{display:block;font-size:11.5px;color:var(--muted)}
.cb-coaches{list-style:none;margin:8px 0 0;padding:0 0 0 18px;border-left:2px dashed var(--hair);margin-left:22px;display:grid;gap:6px}
.cb-coach{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:10px;border:1px solid var(--hair);min-width:0}
.cb-coach.off{opacity:.62}
.cb-coach .grow{flex:1;min-width:0}
.cb-coach .nm{font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cb-coach small{display:block;font-size:11.5px;color:var(--muted)}
.cb-coach select{min-height:30px;border:1px solid var(--hair);border-radius:8px;background:var(--surface);color:var(--ink);font-size:12px;padding:0 6px;max-width:120px}
.cb-coach select:disabled{opacity:.45}
.cb-sw{position:relative;width:38px;height:22px;border-radius:999px;border:0;background:#CBD3CE;cursor:pointer;flex-shrink:0;padding:0}
.cb-sw::after{content:"";position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;background:#fff;transition:transform .18s}
.cb-sw[aria-checked="true"]{background:var(--ochre)}
.cb-sw[aria-checked="true"]::after{transform:translateX(16px)}
.cb-train-f{display:flex;justify-content:flex-end;gap:6px;margin-top:10px}
.cb-coach-ctl{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}

/* coordination */
.cb-coord{padding:14px}
.cb-panes{display:grid;gap:16px;margin-top:12px}
.cb-pane{display:none;min-width:0}
.cb-pane.on{display:block}
.cb-pane h3{display:none;margin:0 0 10px;font-size:13px;font-weight:600}
.cb-list{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.cb-claim{padding:10px 12px;border:1px solid var(--hair);border-radius:10px;display:grid;gap:6px}
.cb-claim.conflict{border-color:var(--ochre);background:rgba(212,166,74,.06)}
.cb-claim.stale{opacity:.7}
.cb-claim-top{display:flex;gap:8px;align-items:center;min-width:0}
.cb-claim-top .cb-mono{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cb-claim-meta{font-size:12px;color:var(--muted)}
.cb-claim-warn{display:flex;gap:6px;align-items:center;font-size:12px;color:#8a6418}
.cb-claim-acts{display:flex;gap:6px;flex-wrap:wrap}
.cb-claim-acts .cb-btn{min-height:30px;padding:0 10px;font-size:12px}
.cb-menu{position:relative}
.cb-menu summary{list-style:none}
.cb-menu summary::-webkit-details-marker{display:none}
.cb-menu-pop{position:absolute;z-index:5;top:calc(100% + 4px);left:0;min-width:180px;padding:6px;background:var(--surface);border:1px solid var(--hair);border-radius:10px;box-shadow:0 12px 30px -16px rgba(15,47,58,.5)}
.cb-menu-pop button{all:unset;cursor:pointer;display:flex;gap:8px;align-items:center;width:100%;box-sizing:border-box;padding:8px 10px;border-radius:7px;font-size:13px}
.cb-menu-pop button:hover{background:var(--ground)}
.cb-worker{display:flex;gap:10px;align-items:center;padding:10px 12px;border:1px solid var(--hair);border-radius:10px;min-width:0}
.cb-worker .grow{flex:1;min-width:0}
.cb-worker .nm{font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cb-worker small{display:block;font-size:11.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cb-group{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:6px 0 2px}
.cb-feed{list-style:none;margin:0;padding:0;position:relative}
.cb-feed li{display:grid;grid-template-columns:44px 1fr;gap:10px;padding:8px 0;border-bottom:1px solid var(--hair)}
.cb-feed li:last-child{border-bottom:0}
.cb-feed time{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;padding-top:1px}
.cb-feed .tx{font-size:13px;line-height:1.4}
.cb-feed .tx small{display:flex;gap:6px;align-items:center;margin-top:3px;font-size:11.5px;color:var(--muted)}
.cb-filter{display:flex;gap:6px;margin-bottom:8px}

/* archive */
.cb-arch{padding:0}
.cb-arch summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:10px;padding:12px 14px;font-size:13px;font-weight:600}
.cb-arch summary::-webkit-details-marker{display:none}
.cb-arch summary .cb-i:last-child{margin-left:auto;transition:transform .18s;color:var(--muted)}
.cb-arch[open] summary .cb-i:last-child{transform:rotate(90deg)}
.cb-arch ul{list-style:none;margin:0;padding:0 14px 12px;display:grid;gap:6px}
.cb-arch li{display:flex;gap:10px;align-items:center;padding:8px 10px;border-radius:10px;background:var(--ground);font-size:12.5px;min-width:0}
.cb-arch li .grow{flex:1;min-width:0}
.cb-arch li small{display:block;color:var(--muted);font-size:11.5px}

/* empty states */
.cb-empty{padding:28px 18px;text-align:center;display:grid;gap:10px;justify-items:center}
.cb-empty .ill{width:48px;height:48px;border-radius:14px;display:grid;place-items:center;background:var(--sky-chip);color:var(--sky-ink)}
.cb-empty h3{margin:0;font-size:15px}
.cb-empty p{margin:0;max-width:380px;font-size:13px;color:var(--muted);line-height:1.45}

/* QR dialog */
.cb-dlg{border:0;border-radius:16px;padding:20px;max-width:min(340px,calc(100vw - 32px));box-shadow:0 30px 60px -30px rgba(15,47,58,.6);color:var(--ink)}
.cb-dlg::backdrop{background:rgba(15,47,58,.35)}
.cb-dlg h3{margin:0 0 4px;font-size:15px}
.cb-dlg p{margin:0 0 14px;font-size:12.5px;color:var(--muted)}
.cb-dlg .qr{display:grid;place-items:center;padding:14px;border-radius:12px;background:#fff;border:1px solid var(--hair)}
.cb-dlg .row{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:14px}

/* tablet */
@media (min-width:768px){
  .cb-bar{flex-direction:row;align-items:center;justify-content:space-between}
  .cb-bar-main{flex:1}
  .cb-bar-acts .sp{display:none}
  .cb-map{display:block}
  .cb-trains{grid-template-columns:repeat(2,minmax(0,1fr))}
}
/* desktop */
@media (min-width:1200px){
  .cb-trains{grid-template-columns:repeat(3,minmax(0,1fr))}
  .cb-coord .cb-seg{display:none}
  .cb-panes{grid-template-columns:1.15fr 1fr 1fr;gap:20px}
  .cb-pane{display:block}
  .cb-pane h3{display:flex;gap:8px;align-items:center}
}
@media (prefers-reduced-motion:reduce){.cb-sw::after,.cb-arch summary .cb-i:last-child{transition:none}}
`;

/* ---------- pieces ---------- */
function billingSelect(mode, enabled) {
  return `<label class="cb-bill">Billing <select data-act="billing" aria-label="Billing mode" ${enabled ? "" : "disabled"}>
      <option value="A"${mode === "A" ? " selected" : ""}>A · each station pays its own agents</option>
      <option value="B"${mode === "B" ? " selected" : ""}>B · master drives, workers pay where they run</option></select></label>`;
}

function sessionBar() {
  const s = S.session;
  if (s) {
    const u = S.usage || {};
    const mode = u.billing_mode || s.billing_mode || "A";
    const host = s.role === "host";
    const n = (s.members || []).length;
    return `<section class="cb-card cb-bar" aria-label="Session">
      <div class="cb-bar-main"><span class="cb-live" aria-hidden="true"></span>
        <div class="cb-bar-ttl"><b>${esc(s.name)}</b><span>${n} ${n === 1 ? "station" : "stations"}${s.created_at || s.started_at ? " · since " + esc(ago(s.created_at || s.started_at)) : ""} · ${tok(u.total)} tokens metered today</span></div>
        ${host ? '<span class="cb-badge gold">You\'re host</span>' : ""}${s.is_master ? '<span class="cb-badge sky">Master</span>' : ""}</div>
      <div class="cb-bar-acts">
        <span class="cb-code" aria-label="Couple code ${esc(s.code)}">${esc(s.code || "––––")}<button type="button" data-act="copy" aria-label="Copy code">${ic("copy", 15)}</button></span>
        ${billingSelect(mode, s.is_master || host)}
        <span class="sp"></span>
        ${host ? "" : '<button class="cb-btn ghost" type="button" data-act="uncouple">Uncouple</button>'}
        ${host ? '<button class="cb-btn danger" type="button" data-act="end">End session</button>' : ""}
      </div>
    </section>`;
  }
  const opts = S.peers.map((p) => `<option value="${esc(slugOf(p))}">${esc(p.name)} · @${esc(slugOf(p))}</option>`).join("");
  return `<section class="cb-card cb-bar" aria-label="Session">
    <div class="cb-bar-main">${ic("link", 20)}<div class="cb-bar-ttl"><b>Not in a session</b><span>Start one and share the code, or couple into a session hosted on a linked station.</span></div></div>
    <form class="cb-join" data-form="start">
      <input name="name" aria-label="Session name" placeholder="Session name" required maxlength="60">
      <select name="billing_mode" aria-label="Billing mode"><option value="A">Billing A</option><option value="B">Billing B</option></select>
      <button class="cb-btn gold" type="submit">Start session</button>
    </form>
    ${S.peers.length ? `<form class="cb-join" data-form="couple">
      <input name="code" aria-label="Couple code" placeholder="Code, e.g. ABC-123" required maxlength="12">
      <select name="station" aria-label="Host station">${opts}</select>
      <button class="cb-btn" type="submit">Couple</button>
    </form>` : ""}
  </section>`;
}

function starMap(trs) {
  // host engine in the middle; other engines around it; coaches fan out around their engine
  const W = 720, H = 250, cx = W / 2, cy = 112;
  const host = trs.find((t) => t.host) || trs[0];
  const others = trs.filter((t) => t !== host);
  const spots = [[150, 112], [570, 112], [150, 210], [570, 210]];
  const pos = new Map([[host.id, [cx, cy]]]);
  others.forEach((t, i) => pos.set(t.id, spots[i % spots.length]));
  let lines = "", nodes = "";
  for (const t of others) {
    const [x, y] = pos.get(t.id);
    const off = t.engine.state === "offline";
    lines += `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="${off ? "#C9D0CB" : "var(--ochre)"}" stroke-width="2" ${off ? 'stroke-dasharray="5 5"' : ""}/>`;
  }
  for (const t of trs) {
    const [x, y] = pos.get(t.id);
    const n = t.coaches.length;
    t.coaches.forEach((c, i) => {
      const a = (-Math.PI / 2) + (i - (n - 1) / 2) * 0.75 + (x < cx ? -Math.PI / 2 : x > cx ? Math.PI / 2 : Math.PI);
      const r = 62, px = x + Math.cos(a) * r, py = y + Math.sin(a) * r * 0.75;
      lines += `<line x1="${x}" y1="${y}" x2="${px.toFixed(1)}" y2="${py.toFixed(1)}" stroke="#D5DBD3" stroke-width="1.5"/>`;
      nodes += `<g><circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="7" fill="${c.state === "offline" ? "#E4E8E2" : "#fff"}" stroke="${c.state === "online" ? "#3E9C6B" : "#B9C2BC"}" stroke-width="2"/><title>${esc(c.name)} · ${esc(stateText(c.state))}</title></g>`;
    });
    const off = t.engine.state === "offline";
    nodes += `<g class="eng"><circle cx="${x}" cy="${y}" r="${t.host ? 20 : 16}" fill="${t.host ? "var(--ochre)" : off ? "#E4E8E2" : "var(--sky-deep)"}"/>
      <text x="${x}" y="${y + 4}" text-anchor="middle" style="fill:#fff;font-weight:700;font-size:12px">${esc(t.person[0] || "?")}</text>
      <text class="nm" x="${x}" y="${y + (t.host ? 38 : 34)}" text-anchor="middle">${esc(t.person)}${t.host ? " · host" : ""}</text>
      <title>${esc(t.engine.name)} (engine) · ${esc(stateText(t.engine.state))}</title></g>`;
  }
  return `<div class="cb-card cb-map" aria-hidden="true"><svg viewBox="0 0 ${W} ${H}" role="img">${lines}${nodes}</svg></div>`;
}

function coachRow(c) {
  return `<li class="cb-coach${c.state === "offline" ? " off" : ""}">${ic(c.kind)}<div class="grow"><div class="nm">${esc(c.name)}</div><small>${dot(c.state)} ${esc(stateText(c.state))}${c.note ? " · " + esc(c.note) : ""}</small></div></li>`;
}

/* The linked station behind a remote train: screen buttons and a way to its permissions. */
function trainPeerRow(t) {
  const pid = (t.usage || {}).peer_id;
  const p = S.peers.find((x) => x.peer_id === pid) || S.peers.find((x) => x.name === t.engine.name || x.name === t.person);
  if (!p) return "";
  return `<div class="pp-scr"><button type="button" class="cb-btn" data-scr="still" data-peer="${esc(p.peer_id)}" data-name="${esc(p.name)}">View screen</button><button type="button" class="cb-btn" data-scr="live" data-peer="${esc(p.peer_id)}" data-name="${esc(p.name)}">Watch live</button></div>
    <a class="cb-muted" style="font-size:12.5px" href="#collab/people">What ${esc(p.name)} may do here →</a>`;
}

function trainCard(t) {
  const card = t.card || {};
  const provs = Array.isArray(card.providers) ? card.providers.map((p) => (typeof p === "string" ? p : p.name || p.id)).filter(Boolean) : [];
  const used = t.usage ? (t.usage.peer_id ? `${tok(t.usage.used_today)} tokens today` : "local, not metered yet") : "";
  return `<article class="cb-card cb-train${t.me ? " me" : ""}" aria-label="${esc(t.person)}'s train">
    <div class="cb-train-h"><span class="cb-av" aria-hidden="true">${esc(t.person[0] || "?")}</span>
      <div class="grow"><b>${esc(t.person)}</b>${t.host ? '<span class="cb-badge gold">Host</span>' : ""}${t.master ? '<span class="cb-badge sky">Master</span>' : ""}${t.me ? '<span class="cb-badge">You</span>' : ""}</div>
      <span class="cb-muted" style="font-size:12px">${t.coaches.length} ${t.coaches.length === 1 ? "coach" : "coaches"}</span></div>
    <div class="cb-eng">${ic(kindIcon(t.engine.kind))}<div class="grow"><span class="nm">${esc(t.engine.name)}</span><small>Engine · ${esc(stateText(t.engine.state))}</small></div>${dot(t.engine.state)}</div>
    ${t.coaches.length ? `<ul class="cb-coaches" aria-label="Coaches">${t.coaches.map(coachRow).join("")}</ul>` : ""}
    <div class="cb-facts">
      ${provs.length ? `<div><span>Providers</span><b>${esc(provs.join(", "))}</b></div>` : ""}
      ${card.models_count != null ? `<div><span>Models</span><b>${esc(card.models_count)}</b></div>` : ""}
      ${used ? `<div><span>Usage</span><b>${esc(used)}</b></div>` : ""}
    </div>
    ${t.me ? "" : trainPeerRow(t)}
    ${t.me ? "" : `<div class="cb-train-f"><button class="cb-btn ghost danger" type="button" data-act="remove" data-dev="${esc(t.id)}">Remove</button></div>`}
  </article>`;
}

/* Coordination: this station (local) + the linked station in view (remote). */
function coordLists() {
  const c = S.coord || {};
  const sides = [["This station", c.local || {}], [c.remote && c.remote.station ? "@" + c.remote.station : "Linked station", c.remote || {}]];
  const claims = [], workers = [], feed = [];
  const seen = new Map();
  for (const [who, side] of sides) {
    for (const cl of side.claims || []) {
      const key = cl.rel || cl.path;
      const item = { ...cl, who, key };
      if (seen.has(key)) { item.conflict = true; seen.get(key).conflict = true; }
      seen.set(key, item);
      claims.push(item);
    }
    for (const w of side.workers || []) workers.push({ ...w, who });
    for (const d of side.recent_decisions || []) feed.push({ ...d, who });
  }
  return { claims, workers, feed, remoteOffline: !!(c.remote && c.remote.offline) };
}

function claimsPane(L) {
  if (!L.claims.length) return `<p class="pp-note">No files are claimed right now.</p>`;
  return `<ul class="cb-list">${L.claims.map((c) => `<li class="cb-claim${c.conflict ? " conflict" : ""}">
      <div class="cb-claim-top"><span class="cb-mono" title="${esc(c.path)}">${esc(c.key)}</span>${c.conflict ? '<span class="cb-badge gold">Collision</span>' : ""}</div>
      <div class="cb-claim-meta">${esc(c.who)} · ${esc(c.holder || "unknown")}${c.purpose ? " · " + esc(c.purpose) : ""} · ${c.expires_in > 0 ? Math.ceil(c.expires_in / 60) + "m left" : "expiring"}</div>
    </li>`).join("")}</ul>`;
}

function workersPane(L) {
  if (!L.workers.length) return `<p class="pp-note">No workers running.</p>`;
  const by = {};
  L.workers.forEach((w) => (by[w.who] = by[w.who] || []).push(w));
  return Object.entries(by).map(([p, ws]) => `<div class="cb-group">${esc(p)}</div><ul class="cb-list">${ws.map((w) =>
    `<li class="cb-worker">${dot(w.status === "running" ? "online" : "idle")}<div class="grow"><div class="nm">${esc(w.role || "Worker")}</div><small><span class="cb-mono">${esc(w.id)}</span></small></div><span class="cb-badge${w.status === "running" ? " sky" : ""}">${esc(w.status || "")}</span></li>`).join("")}</ul>`).join("");
}

function feedPane(L) {
  if (!L.feed.length) return `<p class="pp-note">No decisions on #main yet.</p>`;
  return `<ul class="cb-feed">${L.feed.map((f) => {
    const text = f.message || f.text || f.decision || "";
    const t = f.ts || f.timestamp;
    const when = t ? new Date((t > 1e12 ? t : t * 1000)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
    return `<li><time>${esc(when)}</time><div class="tx">${esc(String(text).slice(0, 400))}<small>${esc(f.author || f.from || f.who)}</small></div></li>`;
  }).join("")}</ul>`;
}

function coordination() {
  const L = coordLists();
  const panes = [["claims", "Claims", L.claims.length, claimsPane], ["workers", "Live workers", L.workers.length, workersPane], ["feed", "Decisions", L.feed.length, feedPane]];
  return `<section class="cb-card cb-coord" aria-label="Coordination">
    <div class="cb-h"><h2>Coordination</h2><p>${L.remoteOffline ? "Linked station offline: showing this station only" : "What this station and the linked station are touching on #main"}</p></div>
    <div class="cb-seg" role="tablist" aria-label="Coordination views">${panes.map(([k, l, n]) => `<button type="button" role="tab" data-pane="${k}" aria-pressed="${ui.pane === k}" aria-selected="${ui.pane === k}">${l}<span class="n">${n}</span></button>`).join("")}</div>
    <div class="cb-panes">${panes.map(([k, l, n, fn]) => `<div class="cb-pane${ui.pane === k ? " on" : ""}" role="tabpanel"><h3>${l} <span class="cb-badge">${n}</span></h3>${fn(L)}</div>`).join("")}</div>
  </section>`;
}

function emptyNoLinks() {
  return `<section class="cb-card cb-empty"><div class="ill">${ic("link", 22)}</div><h3>No linked stations yet</h3>
    <p>Link your other machines, or a collaborator's station, from the Devices page. Then start a session here and share the code.</p>
    <a class="cb-btn primary" href="#devices">Link a station</a></section>`;
}


const PCSS = `
.cb-tabs{display:flex;gap:4px;padding:4px;border:1px solid var(--hair);border-radius:var(--radius);background:var(--surface);width:max-content;max-width:100%;overflow-x:auto}
.cb-tabs a{padding:7px 14px;border-radius:8px;color:var(--muted);text-decoration:none;font-size:13px;font-weight:500;white-space:nowrap}
.cb-tabs a:hover{color:var(--ink)}
.cb-tabs a[aria-current="page"]{background:var(--sky-chip-hi);color:var(--sky-ink)}
.cb-top{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between}
.cb-online{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:12px;color:var(--muted)}
.cb-chip{display:inline-flex;align-items:center;gap:6px;padding:4px 10px 4px 4px;border:1px solid var(--hair);border-radius:999px;background:var(--surface);font-size:12px;color:var(--ink)}
.cb-chip .cb-av{width:22px;height:22px;font-size:11px}
.cb-chip.partner .cb-av{background:var(--sky-chip);color:var(--sky-ink)}
.cb-pill{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;border-radius:999px;background:rgba(212,166,74,.14);color:#8a6418;font-size:12px;font-weight:600;text-decoration:none}
.pp{display:grid;gap:16px}
.pp-side{display:grid;gap:16px;align-content:start}
.pp-mem{padding:14px;display:grid;gap:12px}
.pp-mem-h{display:flex;align-items:center;gap:10px;min-width:0}
.pp-mem-h .grow{flex:1;min-width:0}
.pp-mem-h b{font-size:14px}
.pp-mem-h small{display:flex;gap:6px;align-items:center;font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pp-mem-h select{min-height:32px;border:1px solid var(--hair);border-radius:8px;background:var(--surface);color:var(--ink);font-size:12.5px;padding:0 8px}
.pp-acc{display:grid;gap:8px}
.pp-acc-row{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
.pp-acc-row>span{font-size:12.5px;display:flex;gap:6px;align-items:center}
.pp-acc .cb-seg button{padding:5px 10px;font-size:12px}
.pp-acc .cb-seg button[aria-pressed="true"][data-level="control"]{background:rgba(212,166,74,.18);color:#8a6418}
.pp-foot{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;padding-top:10px;border-top:1px solid var(--hair)}
.pp-foot label{display:flex;gap:8px;align-items:center;font-size:12.5px}
.pp-inv{padding:14px;display:grid;gap:10px}
.pp-inv-row{display:flex;gap:8px;flex-wrap:wrap}
.pp-inv-row select{flex:1;min-width:120px;min-height:36px;border:1px solid var(--hair);border-radius:10px;background:var(--surface);color:var(--ink);font-size:13px;padding:0 10px}
.pp-pend{list-style:none;margin:0;padding:0;display:grid;gap:6px}
.pp-pend li{display:flex;gap:8px;align-items:center;padding:8px 10px;border-radius:10px;background:var(--ground);font-size:12.5px}
.pp-pend li .grow{flex:1}
.pp-note{margin:0;font-size:12px;color:var(--muted);line-height:1.45}
.pp-audit ul{list-style:none;margin:0;padding:0 14px 12px;display:grid;gap:0}
.pp-audit li{display:grid;grid-template-columns:56px 1fr;gap:10px;padding:7px 0;border-bottom:1px solid var(--hair);font-size:12.5px}
.pp-audit li:last-child{border-bottom:0}
.pp-audit time{color:var(--muted);font-variant-numeric:tabular-nums}
.pp-mems{display:grid;gap:12px}
@media (min-width:768px){.pp-mems{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (min-width:1200px){.pp{grid-template-columns:minmax(0,2fr) minmax(300px,1fr);align-items:start}}
`;

const HCSS = `
.cb-roles,.cb-queue{margin-top:12px;padding:14px;display:flex;flex-direction:column;gap:10px}
.cb-queue .cb-group,.cb-roles .cb-list{margin:0}
.cb-queue .cb-worker,.cb-roles .cb-worker{flex-wrap:wrap;row-gap:8px}
.cb-q-acts{display:inline-flex;gap:6px;flex-wrap:wrap}
.cb-files summary{cursor:pointer;font-size:12px;color:var(--muted)}
.cb-files ul{margin:6px 0 0;padding-left:16px;font-size:12px}
.pp-swarm{display:flex;gap:8px;align-items:flex-start;font-size:13px;cursor:pointer}
.pp-swarm input{margin-top:2px}
.pp-scr{display:flex;gap:8px;flex-wrap:wrap}
.pp-scr .cb-btn{flex:1;min-width:120px}
.pp-perms summary{cursor:pointer;font-size:13px;padding:4px 0}
.pp-perms-g{display:grid;grid-template-columns:1fr;gap:4px 12px;align-items:center;padding:6px 0;font-size:13px}
.pp-perms-g label{color:var(--muted);margin-top:6px}
.pp-perms-g select{min-height:36px;width:100%}
@media (min-width:640px){.pp-perms-g{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.pp-perms-g label{margin-top:0}}
@media (max-width:639px){.cb-q-acts{width:100%}.cb-q-acts .cb-btn{flex:1}}
.cb-hist{display:flex;align-items:flex-end;gap:3px;height:44px;padding:4px 0}
.cb-hist span{flex:1;height:100%;display:flex;align-items:flex-end;background:var(--ground);border-radius:3px;position:relative}
.cb-hist i{display:block;width:100%;background:var(--sky-deep);border-radius:3px;min-height:2px}
.cb-hist span.over i{background:var(--ochre)}
.cb-hist-cap{display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}
.cb-facts{display:grid;gap:4px;font-size:12.5px}
.cb-facts div{display:flex;justify-content:space-between;gap:10px}
.cb-facts span{color:var(--muted)}
.cb-facts b{font-weight:500;text-align:right;min-width:0;overflow:hidden;text-overflow:ellipsis}
.cb-bill{display:flex;gap:6px;align-items:center;font-size:12px;color:var(--muted)}
.cb-bill select{min-height:32px;border:1px solid var(--hair);border-radius:8px;background:var(--surface);color:var(--ink);font-size:12.5px;padding:0 8px;max-width:100%}
.cb-join{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.cb-join input,.cb-join select{min-height:36px;border:1px solid var(--hair);border-radius:10px;background:var(--surface);color:var(--ink);font-size:13px;padding:0 10px;flex:1;min-width:120px}
.pp-allow{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.pp-allow input{width:130px;min-height:34px;border:1px solid var(--hair);border-radius:8px;background:var(--surface);color:var(--ink);font-size:13px;padding:0 8px}
.pp-models{display:flex;flex-wrap:wrap;gap:4px}
.pp-models .cb-badge{font-weight:500}
`;

function historyBars(hist, cap) {
  const max = Math.max(1, cap || 0, ...hist.map((d) => d.used || 0));
  const total = hist.reduce((a, d) => a + (d.used || 0), 0);
  return `<div><div class="cb-hist" role="img" aria-label="Tokens used per day, last ${hist.length} days: ${tok(total)} in total">${hist.map((d) =>
      `<span class="${d.cap && d.used > d.cap ? "over" : ""}" title="${esc(d.date)}: ${tok(d.used)} tokens${d.cap ? " of " + tok(d.cap) : ""}"><i style="height:${Math.round(((d.used || 0) / max) * 100)}%"></i></span>`).join("")}</div>
    <div class="cb-hist-cap"><span>${esc((hist[0] || {}).date || "")}</span><span>${tok(total)} in ${hist.length} days</span><span>today</span></div></div>`;
}

function modelsLine(p) {
  const m = S.models[slugOf(p)];
  if (!m) return `<small class="cb-muted">Models: not loaded</small>`;
  if (m.error) return `<small class="cb-muted">Models: ${esc(m.error)}</small>`;
  const list = Array.isArray(m.models) ? m.models : Object.values(m.models || {}).flat();
  const names = list.map((x) => (typeof x === "string" ? x : x.id || x.model || x.name)).filter(Boolean);
  if (!names.length) return `<small class="cb-muted">Models: none shared</small>`;
  return `<div class="pp-models" aria-label="Models">${names.slice(0, 12).map((n) => `<span class="cb-badge">${esc(n)}</span>`).join("")}${names.length > 12 ? `<span class="cb-muted">+${names.length - 12}</span>` : ""}</div>`;
}

function peersPanel() {
  if (!S.loaded) return `<p class="pp-note">Loading linked stations…</p>`;
  if (!S.peers.length) return emptyNoLinks();
  return `<section aria-label="Linked stations"><div class="cb-h"><h2>Linked stations</h2><p>Daily token allowance, usage and models</p></div>
    <div class="pp-mems">${S.peers.map((p) => {
      const a = S.allowance[p.peer_id] || {};
      const hist = S.history[p.peer_id] || [];
      const grants = Object.entries(p.grants || {}).filter(([k, v]) => GRANT_LABELS[k] && v && v !== "off").map(([k, v]) => `${GRANT_LABELS[k]}: ${v}`);
      return `<article class="cb-card pp-mem" aria-label="${esc(p.name)}">
        <div class="pp-mem-h"><span class="cb-av" aria-hidden="true">${esc((p.name || "?")[0])}</span>
          <div class="grow"><b>${esc(p.name)}</b><small>@${esc(slugOf(p))}${p.last_seen ? " · seen " + esc(ago(p.last_seen)) : ""}</small></div></div>
        <div class="pp-allow">
          <label for="al-${esc(p.peer_id)}" class="cb-muted" style="font-size:12.5px">Daily allowance</label>
          <input id="al-${esc(p.peer_id)}" data-peer="${esc(p.peer_id)}" type="number" min="0" step="1000" value="${Number(a.tokens_per_day || 0)}" aria-describedby="al-h-${esc(p.peer_id)}">
          <small id="al-h-${esc(p.peer_id)}" class="cb-muted">${a.tokens_per_day ? `${tok(a.used_today)} used · ${tok(a.left)} left today` : `${tok(a.used_today)} used today · 0 = no limit`}</small>
        </div>
        ${hist.length ? historyBars(hist, a.tokens_per_day) : ""}
        ${modelsLine(p)}
        ${S.grantSchema && S.grantSchema.length ? permsPanel(p) : grants.length ? `<small class="cb-muted">Grants: ${esc(grants.join(" · "))}</small>` : ""}
        ${swarmRow(p)}
        <div class="pp-scr"><button type="button" class="cb-btn" data-scr="still" data-peer="${esc(p.peer_id)}" data-name="${esc(p.name)}">View screen</button><button type="button" class="cb-btn" data-scr="live" data-peer="${esc(p.peer_id)}" data-name="${esc(p.name)}">Watch live</button></div>
      </article>`;
    }).join("")}</div></section>`;
}

function peopleTab() {
  return `<div class="pp">${peersPanel()}</div>`;
}

/* ---------- Modes A/B/C: roles, approval queue, landing ---------- */
const REJ = "/api/reject_worker";
const MODE_HELP = { A: "Waits for the receiving station's initiator", B: "The master launches workers directly", C: "Runs on the owner's checkout; the owner lands the patch" };
const wid = (w) => String(w.id || w.ref || w.worker_id || "");
const wMode = (w) => (w.swarm ? "C" : String(w.mode || w.billing_mode || (w.meta || {}).billing_mode || "").toUpperCase());
const FINAL = /^(completed|done|failed|error|rejected|stopped|cancell?ed|landed|exited)$/i;
const needs = (w) => w.state === "awaiting_land" || /^(pending|awaiting|review)/i.test(String(w.status || ""));

function rolesCard() {
  const s = S.session;
  if (!s) return "";
  const ms = s.members || [];
  const mine = ms.find((m) => m.self) || {};
  return `<section class="cb-card cb-roles" aria-label="Roles">
    <div class="cb-h"><h2>Roles</h2><p>The master sets the mode. Each station names the chat that approves Mode A requests.</p></div>
    <ul class="cb-list">${ms.map((m) => {
      const isM = (s.master_device && m.device_id === s.master_device) || (m.self && s.is_master);
      return `<li class="cb-worker"><div class="grow"><div class="nm">${esc(m.name || m.device_name || m.device_id)}${m.self ? " <small class='cb-muted'>(this station)</small>" : ""}</div><small>Initiator: <span class="cb-mono">${esc(m.local_initiator_chat || "principal")}</span></small></div>${isM ? '<span class="cb-badge sky">Master</span>' : s.is_master ? `<button class="cb-btn ghost" type="button" data-act="master" data-dev="${esc(m.device_id)}">Make master</button>` : ""}</li>`;
    }).join("")}</ul>
    <form class="cb-join" data-form="initiator"><input name="chat" aria-label="Your local initiator chat" placeholder="Chat alias, e.g. principal" maxlength="60" value="${esc(mine.local_initiator_chat || "")}"><button class="cb-btn" type="submit">Set my initiator</button></form>
  </section>`;
}

function qRow(w, local) {
  const id = wid(w), m = local ? "A" : wMode(w);
  const who = local ? (w.requested_by || (w.meta || {}).requested_by || (w.meta || {}).origin || "") : (w.label || "");
  const st = String((w.swarm ? w.state || w.status : w.status) || "");
  const files = Array.isArray(w.files) ? w.files : [];
  let acts = "";
  if (local) acts = `<button class="cb-btn gold" type="button" data-act="lapprove" data-id="${esc(id)}">Approve</button>` + (REJ ? `<button class="cb-btn ghost" type="button" data-act="lreject" data-id="${esc(id)}">Reject</button>` : "");
  else if (w.state === "awaiting_land") acts = `<button class="cb-btn gold" type="button" data-act="land" data-id="${esc(id)}">Land</button>`;
  else if (needs(w)) {
    // The remote approve/reject API takes worker@station, not the bare id.
    const ref = w.station && !id.includes("@") ? `${id}@${w.station}` : id;
    acts = `<button class="cb-btn gold" type="button" data-act="approve" data-id="${esc(ref)}">Approve</button><button class="cb-btn ghost" type="button" data-act="reject" data-id="${esc(ref)}">Reject</button>`;
  }
  const meta = [who, w.paid_by ? "paid by " + w.paid_by : "", w.tokens ? tok(w.tokens) + " tokens" : "", files.length ? files.length + " files" : ""].filter(Boolean).join(" · ");
  return `<li class="cb-worker"><div class="grow"><div class="nm">${esc(w.role || "Worker")} ${m ? `<span class="cb-badge" title="${esc(MODE_HELP[m] || "")}">Mode ${esc(m)}</span>` : ""}</div>
    <small><span class="cb-mono">${esc(id)}</span>${meta ? " · " + esc(meta) : ""}</small>
    ${files.length ? `<details class="cb-files"><summary>Files</summary><ul>${files.slice(0, 50).map((f) => `<li class="cb-mono">${esc(typeof f === "string" ? f : f.path || "")}</li>`).join("")}</ul></details>` : ""}</div>
    <span class="cb-badge${/run/i.test(st) ? " sky" : ""}">${esc(st.replace(/_/g, " "))}</span>${acts ? `<span class="cb-q-acts">${acts}</span>` : ""}</li>`;
}

function queueCard() {
  const live = S.remote.filter((w) => w.state === "awaiting_land" || !FINAL.test(String(w.status || "")));
  const mine = [...S.local.map((w) => qRow(w, true)), ...live.filter(needs).map((w) => qRow(w, false))];
  const away = live.filter((w) => !needs(w)).map((w) => qRow(w, false));
  const u = S.usage || {};
  return `<section class="cb-card cb-queue" aria-label="Approvals and landing">
    <div class="cb-h"><h2>Approvals and landing</h2><p>Mode A requests wait here · Mode C patches land here${u.swarm_total ? ` · ${tok(u.swarm_total)} swarm tokens today, outside allowances` : ""}</p></div>
    <div class="cb-group">Waiting on you</div>${mine.length ? `<ul class="cb-list">${mine.join("")}</ul>` : `<p class="pp-note">Nothing to approve or land.</p>`}
    ${away.length ? `<div class="cb-group">Running on linked stations</div><ul class="cb-list">${away.join("")}</ul>` : ""}
  </section>`;
}

/* Every grant the backend knows, as one labelled control each (swarm keeps its own checkbox). */
function permsPanel(p) {
  const g = p.grants || {};
  const rows = S.grantSchema.filter((x) => x.key !== "swarm" && x.key !== "collab");
  const on = rows.filter((x) => g[x.key] && !["off", "none"].includes(g[x.key])).length;
  return `<details class="pp-perms"><summary>What ${esc(p.name)} may do here <span class="cb-muted">· ${on} of ${rows.length} allowed</span></summary>
    <div class="pp-perms-g">${rows.map((x) => {
      const id = `gr-${esc(p.peer_id)}-${esc(x.key)}`;
      const cur = g[x.key] || x.choices[0].value;
      return `<label for="${id}">${esc(x.label)}</label><select id="${id}" data-grant="${esc(x.key)}" data-gpeer="${esc(p.peer_id)}">${x.choices.map((c) => `<option value="${esc(c.value)}"${c.value === cur ? " selected" : ""}>${esc(c.label)}</option>`).join("")}</select>`;
    }).join("")}</div></details>`;
}

function swarmRow(p) {
  const on = (p.grants || {}).swarm === "on";
  const m = ((S.usage || {}).members || []).find((x) => x.peer_id === p.peer_id) || {};
  return `<label class="pp-swarm"><input type="checkbox" data-swarm="${esc(p.peer_id)}"${on ? " checked" : ""}><span>Let ${esc(p.name)} run its agents on my checkout (Mode C)</span></label>${m.swarm_today ? `<small class="cb-muted">${tok(m.swarm_today)} swarm tokens today · not counted in the allowance</small>` : ""}`;
}

/* ---------- render ---------- */
let wrap = null;
function render() {
  if (!wrap) return;
  const pill = S.session ? `<a class="cb-pill" href="#collab/sessions"><span class="cb-live" style="width:8px;height:8px"></span>In “${esc(S.session.name)}”</a>` : "";
  const tabs = `<div class="cb-top"><nav class="cb-tabs" aria-label="Collab sections">${[["people", "People"], ["sessions", "Sessions"]].map(([k, l]) =>
    `<a href="#collab/${k}"${ui.tab === k ? ' aria-current="page"' : ""}>${l}</a>`).join("")}</nav>${pill}</div>`;
  if (ui.tab === "people") { wrap.innerHTML = tabs + peopleTab(); return; }
  if (!S.loaded) { wrap.innerHTML = tabs + `<p class="pp-note">Loading…</p>`; return; }
  const trs = trains();
  const body = S.session && trs.length
    ? `<section aria-label="Trains"><div class="cb-h"><h2>Trains</h2><p>Engines couple to the host · coaches ride with their engine</p></div>
        ${starMap(trs)}<div class="cb-trains" style="margin-top:12px">${trs.map(trainCard).join("")}</div></section>`
    : (S.peers.length ? "" : emptyNoLinks());
  wrap.innerHTML = `${tabs}${S.error ? `<p class="pp-note">Couldn't load the session: ${esc(S.error)}</p>` : ""}${sessionBar()}${rolesCard()}${queueCard()}${body}${coordination()}`;
}

function onClick(e) {
  const b = e.target.closest("button");
  if (!b || !wrap.contains(b)) return;
  if (b.dataset.pane) { ui.pane = b.dataset.pane; render(); return; }
  const act = b.dataset.act;
  if (act === "copy") { try { navigator.clipboard?.writeText(S.session?.code || ""); toast("Code copied"); } catch (_) {} }
  else if (act === "end") { if (confirm("End this session for everyone?")) post("/api/collab/end", {}, "Session ended"); }
  else if (act === "uncouple") { if (confirm("Leave this session?")) post("/api/collab/leave", {}, "Left the session"); }
  else if (act === "master") { if (confirm("Hand the master role to this station?")) post("/api/collab/master", { master_device: b.dataset.dev }, "Master changed"); }
  else if (act === "approve") post("/api/remote_workers/approve", { ref: b.dataset.id }, "Approved");
  else if (act === "reject") { if (confirm("Reject this request?")) post("/api/remote_workers/reject", { ref: b.dataset.id }, "Rejected"); }
  else if (act === "land") { if (confirm("Land this patch into your checkout?")) post("/api/remote_workers/land", { id: b.dataset.id }, "Landed"); }
  else if (act === "lapprove") post("/api/approve_worker", { id: b.dataset.id }, "Approved");
  else if (act === "lreject" && REJ) { if (confirm("Reject this request?")) post(REJ, { id: b.dataset.id }, "Rejected"); }
  else if (act === "remove") { if (confirm("Remove this station from the session?")) post("/api/collab/remove", { device_id: b.dataset.dev }, "Removed"); }
}

function onSubmit(e) {
  const f = e.target.closest("form[data-form]");
  if (!f) return;
  e.preventDefault();
  const d = Object.fromEntries(new FormData(f));
  if (f.dataset.form === "start") post("/api/collab/start", { name: d.name, billing_mode: d.billing_mode }, "Session started");
  if (f.dataset.form === "initiator") post("/api/collab/local_initiator", { local_initiator_chat: d.chat }, "Initiator set");
  if (f.dataset.form === "couple") post("/api/collab/couple", { code: d.code, station: d.station }, "Coupled");
}

function onChange(e) {
  const t = e.target;
  if (t.dataset.act === "billing") post("/api/collab/billing", { billing_mode: t.value }, "Billing mode " + t.value);
  else if (t.dataset.grant) {
    const lab = (S.grantSchema.find((x) => x.key === t.dataset.grant) || {}).label || t.dataset.grant;
    post("/api/peers/grants", { peer_id: t.dataset.gpeer, grants: { [t.dataset.grant]: t.value } }, `${lab}: ${t.selectedOptions[0].text}`);
  }
  else if (t.dataset.swarm) post("/api/peers/grants", { peer_id: t.dataset.swarm, grants: { swarm: t.checked ? "on" : "off" } }, t.checked ? "Mode C allowed" : "Mode C off");
  else if (t.dataset.peer) {
    const v = Math.max(0, Math.floor(Number(t.value) || 0));
    post("/api/peers/allowance", { peer_id: t.dataset.peer, tokens_per_day: v }, v ? `Allowance set to ${tok(v)}/day` : "Allowance removed");
  }
}

function unmount() {
  document.body.classList.remove("on-collab");
  wrap?.remove(); wrap = null;
}

function mount() {
  unmount();
  const [r, sub] = (location.hash.slice(1) || "home").split("/");
  if (r !== ROUTE && r !== "team") return;
  ui.tab = r === "team" ? (sub === "sessions" ? "sessions" : "people") : (sub === "sessions" ? "sessions" : "people");
  document.body.classList.add("on-collab");
  if (!document.getElementById("cb-css")) document.head.append(Object.assign(document.createElement("style"), { id: "cb-css", textContent: CSS + PCSS + HCSS }));
  wrap = document.createElement("div");
  wrap.className = "cb";
  wrap.addEventListener("click", onClick);
  wrap.addEventListener("submit", onSubmit);
  wrap.addEventListener("change", onChange);
  document.getElementById("page").append(wrap);
  render();
  loadCollab().then(render);
}

document.addEventListener("page:render", mount);
document.addEventListener("collab:data", () => render());
mount();
