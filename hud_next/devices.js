// Devices page prototype — plain light DOM, mounts on route "devices".
import { deviceKind, frameForDeviceType, frameSvg, screenRectStyle } from "./device_frames.js";
import { mountViewer, closeViewerNow } from "./screen.js";
import { fetchIdeState, renderIdeView, IDE_POLL_MS } from "./ide_view.js";
import qrcode from "./qrcode.js";
const ROUTE = "devices";
let wrap = null, timer = 0;

function renderLinkBox(info, noteText, onNew, onClose) {
  const box = el("div", "dv-linkbox");
  if (info.error) {
    box.append(el("div", "dv-empty", info.error));
    const closeBtn = el("button", "dv-btn", "Close");
    closeBtn.addEventListener("click", onClose);
    const row = el("div", "dv-btns");
    row.append(closeBtn);
    box.append(row);
    return box;
  }
  box.append(el("div", "dv-note", noteText));
  if (info.link) box.append(renderQrSvg(info.link));
  const expAt = info.expires_at ? (Number(info.expires_at) > 1e12 ? Number(info.expires_at) : Number(info.expires_at) * 1000) : (info._exp || (info._exp = Date.now() + 300000));
  const timerEl = el("div", "dv-note");
  const fmt = (ms) => {
    const s = Math.max(0, Math.ceil(ms / 1000));
    return s > 0 ? `Expires in ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : "Code expired — click New code";
  };
  timerEl.textContent = fmt(expAt - Date.now());
  const iv = setInterval(() => {
    if (!timerEl.isConnected) { clearInterval(iv); return; }
    const rem = expAt - Date.now();
    timerEl.textContent = fmt(rem);
    if (rem <= 0) clearInterval(iv);
  }, 1000);
  box.append(timerEl);
  const ta = el("textarea", "dv-linktext dv-mono");
  ta.rows = 3; ta.readOnly = true; ta.value = info.link || "";
  box.append(ta);
  const copy = el("button", "dv-btn primary", "Copy");
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(info.link || ""); window.toast?.success("Copied"); }
    catch (_) { ta.select(); }
  });
  const fresh = el("button", "dv-btn", "New code");
  fresh.addEventListener("click", () => { clearInterval(iv); onNew(); });
  const closeBtn = el("button", "dv-btn", "Close");
  closeBtn.addEventListener("click", () => { clearInterval(iv); onClose(); });
  const row = el("div", "dv-btns");
  row.append(copy, fresh, closeBtn);
  box.append(row);
  return box;
}

function renderQrSvg(link) {
  const wrap = document.createElement("div");
  if (!link) return wrap;
  try {
    const code = qrcode(0, "M");
    code.addData(String(link));
    code.make();
    wrap.setAttribute("role", "img");
    wrap.setAttribute("aria-label", "Pairing QR code");
    wrap.style.cssText = "background:#fff;padding:12px;border-radius:12px;width:min(220px,70vw);line-height:0;margin:8px auto";
    wrap.innerHTML = code.createSvgTag({ scalable: true, margin: 0 });
  } catch (_) {}
  return wrap;
}

const CSS = `
.dv-wrap{padding:24px;max-width:1200px;margin:0 auto;box-sizing:border-box}
@media(max-width:480px){.dv-wrap{padding:12px}.dv-card{padding:14px}}
.dv-grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}
.dv-card{background:var(--surface);border:1px solid var(--hair);border-radius:var(--radius);padding:20px;grid-column:span 12}
.dv-mac{grid-column:span 5}
.dv-wait{grid-column:span 7}
@media(min-width:1024px){
  .dv-wrap{display:grid;grid-template-columns:repeat(12,1fr);gap:16px;align-items:start}
  .dv-card{grid-column:auto}
  .dv-mac{grid-column:span 5}
  .dv-wait{grid-column:span 7}
  .dv-paired{grid-column:1/-1}
}
.dv-h2{font-size:15px;font-weight:600;color:var(--ink);margin:0 0 14px}
.dv-mono{font-family:"JetBrains Mono",monospace}
.dv-kv{display:flex;justify-content:space-between;gap:12px;font-size:13px;padding:6px 0;border-bottom:1px solid var(--hair)}
.dv-kv:last-of-type{border-bottom:none}
.dv-kv .k{color:var(--muted)}
.dv-kv .v{color:var(--ink);text-align:right}
.dv-rambar{height:6px;border-radius:999px;background:var(--hair);margin:8px 0 2px;overflow:hidden}
.dv-rambar>div{height:100%;border-radius:999px;background:var(--sky)}
.dv-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.dv-chip{font-size:11px;padding:3px 9px;border-radius:999px;background:var(--sky);color:#fff}
.dv-chip.off{opacity:.5;text-decoration:line-through}
.dv-models{margin:6px 0 0 16px;display:flex;flex-direction:column;gap:4px}
.dv-models .prov{font-size:12px;color:var(--muted,#667);display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.dv-mcps{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin-top:4px;font-size:11px}
.dv-mcp-lbl{font-size:11px;font-weight:600}
.dv-mcp-badge{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--hair);background:var(--surface);color:var(--ink)}
.dv-note{font-size:12px;color:var(--muted);margin-top:10px}
.dv-station{display:flex;align-items:center;gap:8px;margin-top:12px}
.dv-station code{font-family:"JetBrains Mono",monospace;font-size:12px;background:var(--hair);border-radius:8px;padding:5px 8px;overflow-wrap:anywhere}
.dv-iconbtn{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:8px;border:1px solid var(--hair);background:var(--surface);color:var(--muted);cursor:pointer}
.dv-iconbtn:hover{color:var(--ink)}
.dv-req{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--hair)}
.dv-req:last-child{border-bottom:none}
.dv-req .name{font-size:13px;font-weight:600;color:var(--ink)}
.dv-req .code{font-family:"JetBrains Mono",monospace;font-size:22px;letter-spacing:.08em;color:var(--ink)}
.dv-req .exp{font-size:12px;color:var(--muted)}
.dv-req .grow{flex:1 1 auto}
.dv-scope-sel{font-size:12px;padding:6px 8px;border-radius:10px;border:1px solid var(--hair);background:var(--surface);color:var(--ink)}
.dv-btn{font:inherit;font-size:12px;font-weight:600;padding:7px 14px;border-radius:8px;border:1px solid var(--hair);background:var(--surface);color:var(--ink);cursor:pointer}
.dv-btn:hover{background:var(--hair)}
.dv-btn.primary{background:var(--sky);border-color:transparent;color:#fff}
.dv-btn.primary:hover{filter:brightness(.97)}
.dv-btn.danger{color:#c2413a;background:#fdeceb;border-color:transparent}
.dv-btn.danger:hover{filter:brightness(.97)}
.dv-btn.ghost{border-color:transparent;background:transparent;color:var(--muted)}
.dv-btn.ghost:hover{color:var(--ink);background:var(--hair)}
.dv-btn:disabled{opacity:.5;cursor:default}
.dv-confirm{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:12px;color:var(--ink);background:var(--hair);border-radius:8px;padding:8px 10px}
.dv-empty{font-size:13px;color:var(--muted);padding:8px 0}
.dv-fleet{grid-column:1/-1;min-width:0}
.dv-frames{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(220px,100%),260px));gap:18px;margin-top:4px}
.dv-frame{position:relative;display:flex;flex-direction:column;gap:8px;max-width:260px;width:100%;min-width:0}
.dv-frame-media{position:relative;line-height:0;width:100%;max-width:100%}
.dv-frame-media svg{width:100%;height:auto;display:block;position:relative;z-index:2;pointer-events:none}
.dv-screen{position:absolute;z-index:1;overflow:hidden;line-height:1.25;background:linear-gradient(160deg,#0e1524 0%,#131c30 100%);color:#cfe0ff;padding:6px 8px;font-size:10px;display:flex;flex-direction:column;gap:3px;border-radius:2px;box-sizing:border-box}
.dv-frame[data-kind="laptop"] .dv-screen{padding-top:12px}
.dv-frame[data-kind="phone"] .dv-screen{padding-top:20px;border-radius:14px}
.dv-screen-snap{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;z-index:1}
.dv-screen-hud{position:relative;z-index:2;display:flex;flex-direction:column;gap:2px;min-width:0}
.dv-screen.has-snap .dv-screen-hud{background:rgba(10,16,28,.72);padding:3px 5px;border-radius:3px}
.dv-screen .dv-act-title{font-size:11px;font-weight:600;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dv-screen .dv-act-row{display:flex;justify-content:space-between;gap:8px;white-space:nowrap;overflow:hidden}
.dv-screen .dv-act-dim{color:#7d8db0}
.dv-screen .dv-act-bar{height:3px;border-radius:2px;background:#26324e;overflow:hidden;margin-top:1px}
.dv-screen .dv-act-bar>div{height:100%;background:#5aa2ff}
.dv-frame-panel{background:linear-gradient(160deg,#0e1524 0%,#131c30 100%);color:#cfe0ff;border-radius:10px;padding:10px 12px;font-size:11px;display:flex;flex-direction:column;gap:4px;line-height:1.3}
.dv-frame-panel .dv-act-title{font-weight:600;color:#fff}
.dv-frame-panel .dv-act-row{display:flex;justify-content:space-between;gap:10px}
.dv-frame-panel .dv-act-dim{color:#7d8db0}
.dv-frame-actions{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.dv-frame-actions .dv-btn{padding:4px 9px;font-size:11px}
.dv-ide{margin-top:12px;border:1px solid var(--hair);border-radius:10px;padding:12px;background:var(--surface);color:var(--ink);font-size:12px;display:flex;flex-direction:column;gap:8px;overflow-x:auto}
.dv-ide-head{display:flex;justify-content:space-between;align-items:center;gap:8px;font-weight:600}
.dv-ide-sec{display:flex;flex-direction:column;gap:4px}
.dv-ide-lbl{font-size:11px;font-weight:600;color:var(--muted)}
.dv-ide-chips{display:flex;flex-wrap:wrap;gap:6px}
.dv-ide-chip,.dv-ide-tab{font-size:11px;padding:3px 8px;border-radius:6px;border:1px solid var(--hair);background:var(--surface);color:var(--ink)}
.dv-ide-tab{cursor:pointer}
.dv-ide-chip.active,.dv-ide-tab.active{border-color:var(--sky);color:var(--sky-ink)}
.dv-ide-link{background:none;border:none;padding:0;color:var(--sky-ink);cursor:pointer;font:inherit;text-align:left;text-decoration:underline;word-break:break-all}
.dv-ide-code{margin:6px 0 0;padding:8px;border-radius:6px;background:var(--hair);font-family:"JetBrains Mono",monospace;font-size:11px;max-height:220px;overflow:auto;white-space:pre-wrap}
@media(prefers-color-scheme:dark){
  .dv-btn.danger,.dv-tag.full{background:rgba(248,113,113,.16);color:#fca5a5}
  .dv-screen,.dv-frame-panel{border:1px solid rgba(255,255,255,.1)}
}
:root[data-theme="dark"] .dv-btn.danger,:root[data-theme="dark"] .dv-tag.full{background:rgba(248,113,113,.16);color:#fca5a5}
:root[data-theme="dark"] .dv-screen,:root[data-theme="dark"] .dv-frame-panel{border:1px solid rgba(255,255,255,.1)}
.dv-fleet-empty{font-size:13px;color:var(--muted);padding:8px 0}
.dv-table{width:100%;border-collapse:collapse;font-size:13px}
.dv-table th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;padding:8px 10px;border-bottom:1px solid var(--hair)}
.dv-table td{padding:10px;border-bottom:1px solid var(--hair);color:var(--ink);vertical-align:middle}
.dv-table tr:last-child td{border-bottom:none}
.dv-dev{display:inline-flex;align-items:center;gap:8px}
.dv-dev svg{color:var(--muted)}
.dv-namebtn{font:inherit;font-size:13px;color:var(--ink);background:none;border:none;border-bottom:1px dashed transparent;cursor:pointer;padding:0}
.dv-namebtn:hover{border-bottom-color:var(--muted)}
.dv-rename{font:inherit;font-size:13px;border:1px solid var(--hair);border-radius:10px;padding:4px 8px;width:160px}
.dv-tag{display:inline-block;font-size:11px;font-weight:600;padding:3px 10px;border-radius:999px;background:var(--sky);color:#fff}
.dv-tag.full{background:#fdeceb;color:#c2413a}
.dv-tag.read{background:var(--hair);color:var(--muted)}
.dv-dim{color:var(--muted)}
.dv-rel{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:2px 8px;border-radius:999px;background:var(--hair);color:var(--ink);margin-left:6px;vertical-align:1px}
.dv-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--muted);margin-right:6px;vertical-align:0}
.dv-dot.on{background:#22A06B}
.dv-link{color:var(--sky-ink);text-decoration:none;font-size:12px;font-weight:600}
.dv-link:hover{text-decoration:underline}
.dv-details{margin-top:12px}
.dv-details summary{font-size:12px;color:var(--muted);cursor:pointer}
.dv-details .dv-table{opacity:.55}
.dv-linked{grid-column:1/-1}
.dv-head{display:flex;flex-wrap:wrap;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px}
.dv-btns{display:flex;flex-wrap:wrap;gap:8px}
.dv-linkbox,.dv-linkform{display:flex;flex-direction:column;gap:10px;margin:10px 0;padding:12px;border:1px solid var(--hair);border-radius:10px}
.dv-field{display:flex;flex-direction:column;gap:6px}
.dv-label{font-size:12px;color:var(--muted);font-weight:600}
.dv-linktext{width:100%;font:inherit;font-size:12px;padding:8px 10px;border:1px solid var(--hair);border-radius:10px;background:var(--surface);color:var(--ink);resize:vertical}
.dv-rows{margin-top:8px}
@media (prefers-reduced-motion: reduce){*{transition:none!important;animation:none!important}}
`;

const ICONS = {
  phone: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="2" width="10" height="20" rx="2.5"/><path d="M11 18h2"/></svg>',
  tablet: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="2.5"/><path d="M10 18h4"/></svg>',
  laptop: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="11" rx="2"/><path d="M2 19h20"/></svg>',
  copy: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
  trash: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M5 6l1 14h12l1-14"/></svg>',
};

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text instanceof Node) n.append(text); else if (text != null) n.textContent = text;
  return n;
}
function iconBtn(svg, label) {
  const b = el("button", "dv-iconbtn");
  b.type = "button";
  b.title = label;
  b.setAttribute("aria-label", label);
  b.innerHTML = svg; // static inline SVG only
  return b;
}
/* Some pairings (e.g. through the tunnel) arrive without a platform: infer it from the device name. */
function platformOf(d) {
  if (d && d.platform) return d.platform;
  const n = String((d && d.name) || "").toLowerCase();
  return /iphone/.test(n) ? "ios" : /ipad/.test(n) ? "ipados" : /android|pixel|galaxy/.test(n) ? "android" : "";
}
function platformIcon(p) {
  const s = String(p || "").toLowerCase();
  return s.includes("ios") || s.includes("iphone") || s.includes("android") ? ICONS.phone
    : s.includes("ipad") || s.includes("tablet") ? ICONS.tablet : ICONS.laptop;
}
function rel(ts) {
  if (!ts) return "never";
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}
function scopeLabel(s) {
  return s === "full" ? "Full" : s === "read" ? "Read only" : "Device";
}

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

function grantSwitch(label, pressed, deviceId, grant, hint) {
  const row = el("div", "dv-grant");
  const lbl = el("span", "", label);
  if (hint) lbl.title = hint;
  row.append(lbl);
  if (hint) row.append(el("span", "dv-dim", hint));
  const btn = el("button", "dv-switch", pressed ? "On" : "Off");
  btn.type = "button";
  btn.setAttribute("aria-pressed", String(pressed));
  btn.addEventListener("click", async () => {
    const on = btn.getAttribute("aria-pressed") !== "true";
    try {
      await post("/api/devices/grant", { device_id: deviceId, grant, on });
      await load();
    } catch (e) {
      window.toast?.error("Grant failed", { description: String(e.message || e) });
    }
  });
  row.append(btn);
  return row;
}

const qr = { box: null, timer: 0 };

function qrPanel() {
  if (!qr.box) {
    qr.box = el("div", "dv-qr");
    qr.box.style.cssText = "display:none;flex-direction:column;align-items:center;gap:8px;padding:12px 0";
  }
  return qr.box;
}

async function showQr() {
  const box = qrPanel();
  box.style.display = "flex";
  clearInterval(qr.timer);
  box.replaceChildren(el("div", "dv-note", "Making a pairing code…"));
  let res;
  try {
    res = await post("/api/pair/qr", {});
  } catch (e) {
    const msg = /not set up/.test(String(e.message || e)) ? "Start the tunnel first: subway tunnel --url wss://…" : (e.message || String(e));
    box.replaceChildren(el("div", "dv-note", msg));
    return;
  }
  const code = qrcode(0, "M");
  code.addData(res.link);
  code.make();
  // Dark modules on white in both themes: scanners need the contrast.
  const art = el("div");
  art.setAttribute("role", "img");
  art.setAttribute("aria-label", "Pairing QR code");
  art.style.cssText = "background:#fff;padding:12px;border-radius:12px;width:min(240px,70vw);line-height:0";
  art.innerHTML = code.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  const left = el("div", "dv-note");
  left.setAttribute("aria-live", "polite");
  const newBtn = el("button", "dv-btn", "New code");
  newBtn.type = "button";
  newBtn.addEventListener("click", showQr);
  box.replaceChildren(
    art,
    left,
    newBtn,
    el("div", "dv-note", "In the Subway app: Connect → Scan QR from your Mac. Works from any network."),
  );
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

// --- render helpers -------------------------------------------------------

let stationData = null, pairData = null, peersData = null;
let peerModels = null, peerMcps = null, vmsData = [];
const activityData = {}; // peer_id -> /api/peers/activity payload
let activityTimer = 0;
const ACTIVITY_POLL_MS = 5000;
let linkFormOpen = false, myLinkInfo = null, phoneLink = null;
const tickers = new Set();

function clearTicks() { tickers.clear(); }

function renderMac() {
  const card = el("section", "dv-card dv-mac");
  const titleRow = el("div", "dv-title-row");
  titleRow.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:8px";
  const title = el("h2", "dv-h2", stationData?.display_name || "This Mac");
  const renameBtn = el("button", "dv-btn ghost", "Rename");
  renameBtn.addEventListener("click", () => {
    const input = el("input", "dv-input");
    input.type = "text"; input.value = stationData?.display_name || ""; input.maxLength = 60; input.autocomplete = "off";
    const save = el("button", "dv-btn primary", "Save");
    const cancel = el("button", "dv-btn", "Cancel");
    const doSave = async () => {
      try { await post("/api/settings", { station_name: input.value.trim() }); await load(); }
      catch (e) { window.toast?.error?.("Rename failed"); }
    };
    input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); doSave(); } else if (ev.key === "Escape") { ev.preventDefault(); renderAll(); } });
    save.addEventListener("click", doSave);
    cancel.addEventListener("click", () => renderAll());
    titleRow.textContent = "";
    titleRow.append(input, save, cancel);
  });
  titleRow.append(title, renameBtn);
  card.append(titleRow);
  const d = stationData?.device || {};
  const rows = [
    ["Hostname", d.hostname || "—"],
    ["OS / arch", `${d.os || "?"} · ${d.arch || "?"} · ${d.platform || "?"}`],
    ["Version", stationData?.version || "—"],
    ["Providers", (stationData?.providers || []).join(", ") || "none"],
    ["Models", String(stationData?.models_count ?? "—")],
  ];
  for (const [k, v] of rows) {
    const row = el("div", "dv-kv");
    row.append(el("span", "k", k), el("span", "v", v));
    card.append(row);
  }
  // RAM bar
  const ram = el("div");
  ram.append(el("div", "k dv-kv").appendChild(el("span", "k", "RAM")) && el("span"));
  ram.textContent = "";
  const ramRow = el("div", "dv-kv");
  ramRow.append(el("span", "k", "RAM"));
  const free = Number(d.free_ram_gb), total = Number(d.total_ram_gb);
  ramRow.append(el("span", "v", Number.isFinite(free) && Number.isFinite(total) && total > 0
    ? `${free} GB free of ${total} GB` : "—"));
  ram.append(ramRow);
  if (total > 0 && free >= 0) {
    const bar = el("div", "dv-rambar");
    const fill = el("div");
    fill.style.width = `${Math.min(100, Math.max(0, ((total - free) / total) * 100))}%`;
    bar.append(fill);
    ram.append(bar);
  }
  card.append(ram);
  // Providers chips + models count
  const chips = el("div", "dv-chips");
  for (const p of stationData?.providers || []) chips.append(el("span", "dv-chip", p));
  if ((stationData?.providers || []).length) card.append(chips);
  // Routes
  const routes = stationData?.routes || [];
  const rk = el("div", "dv-kv");
  rk.append(el("span", "k", "Configured routes"));
  rk.append(el("span", "v", routes.length ? routes.join(", ") : "none"));
  card.append(rk);
  card.append(el("div", "dv-note", "Configured (not checked live)"));
  // station id + copy (behind a disclosure)
  const det = el("details", "dv-details");
  det.append(el("summary", "", "Details"));
  const st = el("div", "dv-station");
  const code = el("code", "", stationData?.station_id || "—");
  st.append(code);
  const cp = iconBtn(ICONS.copy, "Copy station id");
  cp.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(stationData?.station_id || ""); window.toast?.success("Station id copied"); }
    catch { window.toast?.error("Copy failed", { description: "Clipboard unavailable" }); }
  });
  st.append(cp);
  det.append(st);
  card.append(det);
  return card;
}

function renderRequests() {
  const card = el("section", "dv-card dv-wait");
  const head = el("div");
  head.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px";
  const h2 = el("h2", "dv-h2", "Waiting to pair");
  h2.style.margin = "0";
  const qrBtn = el("button", "dv-btn", "Pair with QR");
  qrBtn.type = "button";
  qrBtn.addEventListener("click", showQr);
  head.append(h2, qrBtn);
  card.append(head, qrPanel());
  const reqs = pairData?.requests || [];
  if (!reqs.length) {
    card.append(el("div", "dv-empty", "No devices waiting."));
    const pairBtn = el("button", "dv-btn primary", "Pair a phone");
    pairBtn.addEventListener("click", async () => {
      try { phoneLink = await post("/api/pair/qr", {}); }
      catch (e) { phoneLink = { error: String(e?.message || e) }; }
      renderAll();
    });
    card.append(pairBtn);
    if (phoneLink) {
      card.append(renderLinkBox(
        phoneLink,
        "Open the Subway app → Connect → paste or scan.",
        async () => {
          try { phoneLink = await post("/api/pair/qr", {}); }
          catch (e) { phoneLink = { error: String(e?.message || e) }; }
          renderAll();
        },
        () => { phoneLink = null; renderAll(); }
      ));
    }
    return card;
  }
  for (const r of reqs) card.append(requestRow(r));
  return card;
}

function requestRow(r) {
  const row = el("div", "dv-req");
  const left = el("div");
  left.append(el("div", "name", r.device || "Unknown device"));
  const exp = el("div", "exp");
  const updateExp = () => {
    const s = Math.max(0, Math.floor((r.expires_at || 0) - Date.now() / 1000));
    exp.textContent = `expires in ${Math.floor(s / 60)}m ${s % 60}s`;
  };
  updateExp();
  tickers.add(updateExp);
  left.append(exp);
  row.append(left);
  row.append(el("div", "code dv-mono grow", r.code || ""));
  const sel = el("select", "dv-scope-sel");
  for (const [v, label] of [["device", "Device"], ["read", "Read only"], ["full", "Full"]]) {
    const o = el("option", "", label);
    o.value = v;
    sel.append(o);
  }
  row.append(sel);
  const ok = el("button", "dv-btn primary", "Approve");
  const no = el("button", "dv-btn ghost", "Deny");
  ok.addEventListener("click", async () => {
    const scope = sel.value;
    const act = () => post("/api/pair/approve", { request_id: r.request_id, scope })
      .then(() => window.toast?.success(`${r.device || "Device"} paired (${scopeLabel(scope)})`))
      .catch((e) => window.toast?.error("Approve failed", { description: String(e.message || e) }))
      .finally(load);
    if (scope === "full") inlineConfirm(row, `Grant Full access to ${r.device || "this device"}?`, "Approve", act);
    else act();
  });
  no.addEventListener("click", () => {
    inlineConfirm(row, `Deny ${r.device || "this request"}?`, "Deny", () =>
      post("/api/pair/deny", { request_id: r.request_id })
        .then(() => window.toast?.success("Request denied"))
        .catch((e) => window.toast?.error("Deny failed", { description: String(e.message || e) }))
        .finally(load));
  });
  row.append(ok, no);
  return row;
}

function inlineConfirm(container, text, verb, act) {
  const old = container.querySelector(".dv-confirm");
  if (old) old.remove();
  const c = el("div", "dv-confirm");
  c.append(el("span", "", text));
  const yes = el("button", "dv-btn danger", verb);
  const cancel = el("button", "dv-btn ghost", "Cancel");
  yes.addEventListener("click", () => { c.remove(); act(); });
  cancel.addEventListener("click", () => c.remove());
  c.append(yes, cancel);
  container.append(c);
}

const screenSnapshots = {};
let activeIdePeer = null, ideData = null, ideTimer = 0;

function frameTypeOf(p) {
  return deviceKind(p);
}

function stopIdePoll() {
  clearInterval(ideTimer);
  ideTimer = 0;
}

async function refreshIde() {
  if (!activeIdePeer) return;
  ideData = await fetchIdeState(activeIdePeer.peer_id);
  if (wrap) renderAll();
}

function openIdeFor(p, name) {
  if (activeIdePeer && activeIdePeer.peer_id === p.peer_id) {
    activeIdePeer = null;
    ideData = null;
    stopIdePoll();
    renderAll();
    return;
  }
  activeIdePeer = { peer_id: p.peer_id, name };
  ideData = null;
  stopIdePoll();
  ideTimer = setInterval(refreshIde, IDE_POLL_MS);
  renderAll();
  refreshIde();
}

function snapSrcOf(raw) {
  if (!raw || typeof raw !== "string") return "";
  if (raw.startsWith("data:image/")) return raw;
  const clean = raw.replace(/[^A-Za-z0-9+/=]/g, "");
  return clean ? `data:image/jpeg;base64,${clean}` : "";
}

async function refreshActivity() {
  const peers = peersData?.peers || [];
  await Promise.all(peers.map(async (p) => {
    if (!p?.peer_id) return;
    try {
      const r = await fetch("/api/peers/activity?peer_id=" + encodeURIComponent(p.peer_id));
      activityData[p.peer_id] = r.ok ? await r.json() : { error: `status ${r.status}` };
    } catch (e) {
      activityData[p.peer_id] = { error: String(e.message || e) };
    }
    if (p?.grants?.screen === "snapshot") {
      try {
        const sr = await fetch("/api/screen/remote", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ peer_id: p.peer_id, op: "snapshot", max_w: 600 }),
        }).then((res) => res.ok ? res.json() : null).catch(() => null);
        if (sr?.image_b64) screenSnapshots[p.peer_id] = sr.image_b64;
      } catch (_) {}
    }
  }));
  if (wrap) renderAll();
}

function topModelsOf(st, co, a) {
  const src = st.top_models || a.top_models || st.models || a.models;
  const out = [];
  if (Array.isArray(src)) {
    for (const m of src) {
      const name = typeof m === "string" ? m : (m?.model || m?.name || "");
      if (name && !out.includes(name)) out.push(name);
    }
  }
  if (!out.length && Array.isArray(co.workers)) {
    for (const w of co.workers) {
      const name = w?.model || "";
      if (name && !out.includes(name)) out.push(name);
    }
  }
  return out.length ? out.slice(0, 2).join(", ") : "—";
}

function activityRows(p) {
  const a = activityData[p.peer_id];
  const rows = [];
  if (!a) { rows.push(["", "Loading activity…", true]); return rows; }
  if (a.error) {
    rows.push(["Status", "offline"]);
    rows.push(["", "Unreachable", true]);
    return rows;
  }
  const st = a.station || {};
  const co = a.coordination || {};
  const online = a.ok || p.online === true;
  rows.push(["Status", online ? "online" : "offline"]);
  const person = co.person || st.person || p.person || "—";
  rows.push(["User", person]);
  rows.push(["Principal", st.default_initiator || "principal"]);
  const workers = Array.isArray(co.workers) ? co.workers.filter(w => w && !/done|error|archived/i.test(String(w.status || ""))) : [];
  rows.push(["Workers", workers.length ? `${workers.length} (${workers.slice(0, 2).map(w => w.role || w.worker_id || w.id || "worker").join(", ")}${workers.length > 2 ? ` +${workers.length - 2}` : ""})` : "0 running"]);
  rows.push(["Models", topModelsOf(st, co, a)]);
  const claims = Array.isArray(co.claims) ? co.claims : [];
  rows.push(["Claims", claims.length ? claims.slice(0, 2).map(c => c.rel || String(c.path || "").split("/").pop()).join(", ") + (claims.length > 2 ? ` +${claims.length - 2}` : "") : "none"]);
  const ql = st.quota_left && typeof st.quota_left === "object" ? Object.values(st.quota_left).filter(v => typeof v === "number") : [];
  if (ql.length) rows.push(["Quota left", String(Math.min(...ql))]);
  else if (!a.ok) rows.push(["", a.station_error || a.coordination_error || "stale", true]);
  return rows;
}

function renderFleetScreens() {
  const peers = peersData?.peers || [];
  const card = el("section", "dv-card dv-fleet");
  card.append(el("h2", "dv-h2", "Fleet screens"));
  if (!peers.length) {
    card.append(el("div", "dv-fleet-empty", "Link another station to see its screen here."));
    return card;
  }
  const grid = el("div", "dv-frames");
  for (const p of peers) {
    const frame = frameForDeviceType(deviceKind(p));
    const tile = el("div", "dv-frame");
    tile.dataset.kind = frame.type;
    const media = el("div", "dv-frame-media");
    media.innerHTML = frameSvg(frame.type);
    const rows = activityRows(p);
    const name = p.alias || p.station?.name || p.name || "Station";
    const fill = (box) => {
      box.append(el("div", "dv-act-title", name));
      for (const [k, v, dim] of rows) {
        const r = el("div", "dv-act-row" + (dim ? " dv-act-dim" : ""));
        if (k) r.append(el("span", "dv-act-dim", k));
        r.append(el("span", "", String(v)));
        box.append(r);
      }
    };
    const a = activityData[p.peer_id];
    const snap = snapSrcOf(a?.snapshot_b64 || p?.snapshot_b64 || p?.screen_snapshot || screenSnapshots[p.peer_id]);
    const style = screenRectStyle(frame);
    if (style) {
      const screen = el("div", "dv-screen" + (snap ? " has-snap" : ""));
      Object.assign(screen.style, style);
      if (snap) {
        const simg = el("img", "dv-screen-snap");
        simg.src = snap;
        simg.alt = `${name} screen snapshot`;
        screen.append(simg);
      }
      const hud = el("div", "dv-screen-hud");
      fill(hud);
      screen.append(hud);
      media.append(screen);
      tile.append(media);
    } else {
      const panel = el("div", "dv-frame-panel");
      fill(panel);
      tile.append(media);
      tile.append(panel);
    }
    const acts = el("div", "dv-frame-actions");
    const screenGrant = p?.grants?.screen;
    if (screenGrant === "live" || screenGrant === "snapshot") {
      const scrBtn = el("button", "dv-btn", "View screen");
      scrBtn.type = "button";
      scrBtn.addEventListener("click", () => mountViewer(p.peer_id, name, screenGrant === "live"));
      acts.append(scrBtn);
    }
    // Terminal row: audited-command counter + grant toggle. Own stations only.
    const termRow = el("div", "dv-term-row");
    termRow.style.display = "flex";
    termRow.style.alignItems = "center";
    termRow.style.flexWrap = "wrap";
    termRow.style.gap = "8px";
    termRow.style.width = "100%";
    const termLabel = el("span", "dv-term-label", "Terminal");
    termLabel.style.flex = "1 1 auto";
    termLabel.style.minWidth = "72px";
    termRow.append(termLabel);
    const act = activityData[p.peer_id];
    let audited = null;
    if (act && !act.error) {
      const cand = act.audit_count ?? act.audited_count ?? act.audited_commands
        ?? act.station?.audit_count ?? act.station?.audited_count;
      if (cand != null && cand !== "" && !isNaN(Number(cand))) audited = Number(cand);
    }
    const countText = audited == null ? "—" : `${audited} audited`;
    termRow.append(el("span", "dv-term-count" + (audited == null ? " dv-act-dim" : ""), countText));
    const termOn = String(p?.grants?.terminal || "off") === "on";
    const termToggle = el("input", "dv-term-toggle");
    termToggle.type = "checkbox";
    termToggle.checked = termOn;
    termToggle.disabled = p?.relation !== "own";
    termToggle.title = termToggle.disabled
      ? "Terminal commands require an 'own' linked station"
      : (termOn ? "Disable terminal commands" : "Enable terminal commands");
    termToggle.setAttribute("aria-label", "Terminal access for " + name);
    const revert = () => { termToggle.checked = termOn; };
    termToggle.addEventListener("change", () => {
      post("/api/peers/grants", {
        peer_id: p.peer_id,
        grants: { terminal: termToggle.checked ? "on" : "off" },
      }).then(() => { if (wrap) renderAll(); }).catch(() => {
        revert();
        window.toast?.("Could not update terminal grant");
      });
    });
    termRow.append(termToggle);
    acts.append(termRow);
    // Sandbox row: per-peer sandbox floor grant. Partner peers are clamped to at least "os".
    const sandboxRow = el("div", "dv-sandbox-row");
    sandboxRow.style.display = "flex";
    sandboxRow.style.alignItems = "center";
    sandboxRow.style.flexWrap = "wrap";
    sandboxRow.style.gap = "8px";
    sandboxRow.style.width = "100%";
    const sandboxLabel = el("span", "dv-sandbox-label", "Sandbox");
    sandboxLabel.style.flex = "1 1 auto";
    sandboxLabel.style.minWidth = "72px";
    sandboxRow.append(sandboxLabel);
    const isPartner = p?.relation !== "own";
    if (isPartner) {
      sandboxRow.append(el("span", "dv-act-dim", "partner: min os"));
    }
    const sandboxCur = String(p?.grants?.sandbox || "os");
    const sandboxSel = el("select", "dv-sandbox-select");
    sandboxSel.setAttribute("aria-label", "Sandbox grant for " + name);
    sandboxSel.title = isPartner
      ? "Partner peers are clamped to at least os"
      : "Execution sandbox floor for this peer";
    for (const optVal of ["off", "worktree", "os", "vm"]) {
      const opt = el("option", "", optVal);
      opt.value = optVal;
      if (optVal === sandboxCur) opt.selected = true;
      sandboxSel.append(opt);
    }
    let prevSandbox = sandboxCur;
    sandboxSel.addEventListener("change", () => {
      const nextVal = sandboxSel.value;
      post("/api/peers/grants", {
        peer_id: p.peer_id,
        grants: { sandbox: nextVal },
      }).then(() => {
        prevSandbox = nextVal;
        if (wrap) renderAll();
      }).catch(() => {
        sandboxSel.value = prevSandbox;
        window.toast?.("Could not update sandbox grant");
      });
    });
    sandboxRow.append(sandboxSel);
    acts.append(sandboxRow);
    const ideBtn = el("button", "dv-btn", activeIdePeer?.peer_id === p.peer_id ? "Hide IDE" : "IDE");
    ideBtn.type = "button";
    ideBtn.addEventListener("click", () => openIdeFor(p, name));
    acts.append(ideBtn);
    const chatsBtn = el("button", "dv-btn ghost", "Chats");
    chatsBtn.type = "button";
    chatsBtn.addEventListener("click", () => { location.hash = "#chats"; });
    acts.append(chatsBtn);
    const workersBtn = el("button", "dv-btn ghost", "Workers");
    workersBtn.type = "button";
    workersBtn.addEventListener("click", () => { location.hash = "#workers"; });
    acts.append(workersBtn);
    tile.append(acts);
    const mcps = renderPeerMcps(p);
    if (mcps) tile.append(mcps);
    grid.append(tile);
  }
  card.append(grid);
  if (activeIdePeer) {
    const ideHost = el("div");
    renderIdeView(ideHost, activeIdePeer.peer_id, activeIdePeer.name, ideData, {
      onClose: () => { activeIdePeer = null; ideData = null; stopIdePoll(); renderAll(); },
    });
    card.append(ideHost);
  }
  return card;
}

function renderPaired() {
  const card = el("section", "dv-card dv-paired");
  card.append(el("h2", "dv-h2", "Paired devices"));
  const peerIds = new Set((peersData?.peers || []).map((p) => p?.device_id).filter(Boolean));
  const isStation = (d) => String(d?.platform || d?.kind || "").toLowerCase() === "station" || (d?.device_id && peerIds.has(d.device_id));
  const all = (pairData?.devices || []).filter((d) => !isStation(d));
  const active = all.filter((d) => !d.revoked).sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0));
  const revoked = all.filter((d) => d.revoked);
  const addGlobal = (label, grant) => {
    const row = el("div", "dv-global");
    row.append(el("span", "label", label));
    const on = active.some((d) => (d.grants || []).includes(grant));
    const btn = el("button", "dv-switch", on ? "On" : "Off");
    btn.type = "button";
    btn.setAttribute("aria-pressed", String(on));
    btn.addEventListener("click", async () => {
      const nextOn = btn.getAttribute("aria-pressed") !== "true";
      try {
        await post("/api/devices/grant", { device_id: "*", grant, on: nextOn });
        await load();
      } catch (e) {
        window.toast?.error("Grant failed", { description: String(e.message || e) });
      }
    });
    row.append(btn);
    card.append(row);
  };
  addGlobal("All devices can change settings", "settings.write");
  addGlobal("All devices can send to chats", "chat.send");
  addGlobal("All devices can edit files", "files.write");
  if (!active.length) card.append(el("div", "dv-empty", "No paired devices yet."));
  else {
    const table = el("table", "dv-table");
    const thead = el("thead");
    const trh = el("tr");
    for (const h of ["Name", "Platform", "Scope", "Grants", "Paired", "Last seen", ""]) trh.append(el("th", "", h));
    thead.append(trh);
    table.append(thead);
    const tbody = el("tbody");
    for (const d of active) tbody.append(deviceRow(d));
    table.append(tbody);
    card.append(table);
  }
  if (revoked.length) {
    const det = el("details", "dv-details");
    det.append(el("summary", "", `Revoked (${revoked.length})`));
    const table = el("table", "dv-table");
    const tbody = el("tbody");
    for (const d of revoked) {
      const tr = el("tr");
      tr.append(el("td", "dv-dim", d.name || "—"));
      tr.append(el("td", "dv-dim", platformOf(d) || "—"));
      const tdc = el("td");
      tdc.append(el("span", `dv-tag ${d.scope === "full" ? "full" : d.scope === "read" ? "read" : ""}`, scopeLabel(d.scope)));
      tr.append(tdc);
      tr.append(el("td", "dv-dim", rel(d.created_at)));
      tr.append(el("td", "dv-dim", d.revoked_at ? `${rel(d.revoked_at)} (revoked)` : "revoked"));
      tr.append(el("td"));
      tbody.append(tr);
    }
    table.append(tbody);
    det.append(table);
    card.append(det);
  }
  return card;
}

function deviceRow(d) {
  const tr = el("tr");
  const nameTd = el("td");
  const devWrap = el("div", "dv-dev");
  const nameBtn = el("button", "dv-namebtn", d.name || "Unnamed");
  nameBtn.addEventListener("click", () => {
    devWrap.textContent = "";
    const input = el("input", "dv-rename dv-mono");
    input.value = d.name || "";
    input.maxLength = 60;
    const save = async () => {
      const name = input.value.trim();
      if (!name || name === d.name) { renderAll(); return; }
      try {
        await post("/api/pair/rename", { device_id: d.device_id, name });
        window.toast?.success("Device renamed");
      } catch (e) { window.toast?.error("Rename failed", { description: String(e.message || e) }); }
      load();
    };
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); save(); }
      else if (ev.key === "Escape") renderAll();
    });
    input.addEventListener("blur", () => renderAll());
    devWrap.append(input);
    input.focus();
    input.select();
  });
  const platTd = el("td");
  const pw = el("div", "dv-dev");
  pw.insertAdjacentHTML("afterbegin", platformIcon(platformOf(d)));
  pw.append(el("span", "", platformOf(d) || "—"));
  platTd.append(pw);
  const scopeTd = el("td");
  const s = d.scope;
  scopeTd.append(el("span", `dv-tag ${s === "full" ? "full" : s === "read" ? "read" : ""}`, scopeLabel(s)));
  nameTd.append(devWrap);
  devWrap.append(nameBtn);
  const grantsTd = el("td");
  const grantsWrap = el("div", "dv-grants");
  const grants = Array.isArray(d.grants) ? d.grants : [];
  grantsWrap.append(
    grantSwitch("Run terminal commands", grants.includes("terminal.run"), d.device_id, "terminal.run"),
    grantSwitch("Change settings remotely", grants.includes("settings.write"), d.device_id, "settings.write"),
    grantSwitch("Send to chats", grants.includes("chat.send"), d.device_id, "chat.send"),
    grantSwitch("Allow file edits", grants.includes("files.write"), d.device_id, "files.write", "Lets this phone save files"),
  );
  grantsTd.append(grantsWrap);
  tr.append(nameTd, platTd, scopeTd, grantsTd,
    el("td", "dv-dim", rel(d.created_at)),
    el("td", "dv-dim", rel(d.last_seen)));
  const actTd = el("td");
  const rv = el("button", "dv-btn danger");
  rv.append(iconBtn(ICONS.trash, "Revoke"));
  rv.title = "Revoke";
  rv.addEventListener("click", () => {
    inlineConfirm(tr, `Revoke ${d.name || "this device"}? It loses access now.`, "Revoke", () =>
      post("/api/pair/revoke", { device_id: d.device_id })
        .then(() => window.toast?.success(`${d.name || "Device"} revoked`))
        .catch((e) => window.toast?.error("Revoke failed", { description: String(e.message || e) }))
        .finally(load));
  });
  actTd.append(rv);
  tr.append(actTd);
  return tr;
}

function renderPeerModels(p) {
  const box = el("div", "dv-models");
  if (peerModels === null) { box.append(el("div", "dv-dim", "Loading models…")); return box; }
  const keys = [p.slug, p.alias, p.name, p.station?.name, p.peer_id];
  let entry = null;
  for (const k of keys) { if (k && peerModels[k]) { entry = peerModels[k]; break; } }
  if (!entry) { box.append(el("div", "dv-dim", "Models: unknown")); return box; }
  if (entry.error) { box.append(el("div", "dv-dim", "Models: " + entry.error)); return box; }
  const models = Array.isArray(entry.models) ? entry.models : [];
  if (!models.length) { box.append(el("div", "dv-dim", "Models: none shared")); return box; }
  const byProv = new Map();
  for (const m of models) {
    const prov = m?.provider || "unknown";
    if (!byProv.has(prov)) byProv.set(prov, []);
    byProv.get(prov).push(m);
  }
  for (const [prov, list] of byProv) {
    const line = el("div", "prov");
    line.append(document.createTextNode(`${prov} · ${list.length}`));
    for (const m of list.slice(0, 6)) {
      const chip = el("span", `dv-chip${m.exhausted ? " off" : ""}`, m.model || "?");
      if (m.exhausted) chip.title = "exhausted";
      line.append(chip);
    }
    if (list.length > 6) line.append(el("span", "dv-dim", `+${list.length - 6} more`));
    box.append(line);
  }
  return box;
}

function renderPeerMcps(p) {
  if (!p || p.relation !== "own" || !peerMcps) return null;
  const keys = [p.peer_id, p.slug, p.alias, p.name, p.station?.name];
  let entry = null;
  for (const k of keys) { if (k && peerMcps[k]) { entry = peerMcps[k]; break; } }
  const mcps = Array.isArray(entry?.mcps)
    ? entry.mcps.filter((m) => typeof m === "string" && m.trim()).map((m) => m.trim())
    : [];
  if (!mcps.length) return null;
  const box = el("div", "dv-mcps");
  box.append(el("span", "dv-dim dv-mcp-lbl", "MCP:"));
  for (const name of mcps) {
    box.append(el("span", "dv-mcp-badge", name));
  }
  return box;
}

function renderLinkedStations() {
  const card = el("section", "dv-card dv-linked");
  const head = el("div", "dv-head");
  head.append(el("h2", "dv-h2", "Linked stations"));
  const btns = el("div", "dv-btns");
  const showBtn = el("button", "dv-btn", "Show my link");
  const linkBtn = el("button", "dv-btn primary", "Link a station");
  showBtn.addEventListener("click", async () => {
    try {
      myLinkInfo = await post("/api/pair/qr", {});
      linkFormOpen = false;
    } catch (e) {
      myLinkInfo = { error: /not set up/.test(String(e.message || e))
        ? "Start the tunnel first: subway tunnel --url wss://…"
        : String(e.message || e) };
      linkFormOpen = false;
    }
    renderAll();
  });
  linkBtn.addEventListener("click", () => { linkFormOpen = !linkFormOpen; myLinkInfo = null; renderAll(); });
  btns.append(showBtn, linkBtn);
  head.append(btns);
  card.append(head);

  const peers = peersData?.peers || [];
  if (myLinkInfo) {
    card.append(renderLinkBox(
      myLinkInfo,
      "Paste it into the other station: Devices → Link a station. It works once and expires in a few minutes.",
      async () => {
        try { myLinkInfo = await post("/api/pair/qr", {}); }
        catch (e) { myLinkInfo = { error: String(e?.message || e) }; }
        renderAll();
      },
      () => { myLinkInfo = null; renderAll(); }
    ));
  }

  if (linkFormOpen) {
    const form = el("form", "dv-linkform");
    const ta = el("textarea", "dv-linktext dv-mono");
    ta.rows = 3; ta.placeholder = "subway://pair?…"; ta.required = true; ta.autocomplete = "off";
    const sel = el("select", "dv-scope-sel");
    for (const [v, l] of [["personal", "Personal"], ["collaborator", "Collaborator"], ["observer", "Observer"]]) {
      const o = el("option", "", l); o.value = v; sel.append(o);
    }
    const relSel = el("select", "dv-scope-sel");
    for (const [v, l] of [["own", "Mine"], ["partner", "Someone else's"]]) {
      const o = el("option", "", l); o.value = v; relSel.append(o);
    }
    const syncRel = () => { relSel.value = sel.value === "personal" ? "own" : "partner"; };
    sel.addEventListener("change", syncRel);
    syncRel();
    const out = el("div", "dv-note");
    const row1 = el("div", "dv-field"); row1.append(el("div", "dv-label", "Link from the other station"), ta);
    const row2 = el("div", "dv-field"); row2.append(el("div", "dv-label", "Access for it here"), sel);
    const row3 = el("div", "dv-field"); row3.append(el("div", "dv-label", "Whose station?"), relSel);
    form.append(row1, row2, row3, el("div", "dv-note", "On the other station: Devices → Linked stations → Show my link. Codes work once and expire."), out);
    const submit = el("button", "dv-btn primary", "Link"); submit.type = "submit";
    const btns = el("div", "dv-btns"); btns.append(submit); form.append(btns);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      out.textContent = "Linking through Subway Tunnel…";
      try {
        const res = await post("/api/peer/link", { link: ta.value.trim(), preset: sel.value, relation: relSel.value });
        if (res && res.ok) {
          window.toast?.success(`Linked ${res.peer?.name || "station"}`);
          linkFormOpen = false; myLinkInfo = null;
          peersData = await fetch("/api/peers").then((r) => r.json()).catch(() => peersData);
          renderAll();
        } else out.textContent = res?.error || "Link failed";
      } catch (err) { out.textContent = String(err.message || err); }
    });
    card.append(form);
  }

  if (peers.length) {
    const list = el("div", "dv-rows");
    for (const p of peers) {
      const row = el("div", "dv-req");
      const nameLine = el("div", "name");
      const online = p.online ?? (p.last_seen != null ? (Date.now() / 1000 - Number(p.last_seen) < 300) : null);
      if (online != null) nameLine.append(el("span", `dv-dot${online ? " on" : ""}`));
      const nameText = p.alias || p.station?.name || p.name || "Station";
      const hostText = p.station?.name || p.name || "";
      nameLine.append(document.createTextNode(nameText));
      if (p.alias && hostText && hostText !== p.alias) nameLine.append(el("span", "dv-dim", " " + hostText));
      const rel = p.relation;
      if (rel === "own" || rel === "partner") nameLine.append(el("span", "dv-rel", rel));
      row.append(nameLine);
      row.append(renderPeerModels(p));
      const mcps = renderPeerMcps(p);
      if (mcps) row.append(mcps);
      const sub = el("div", "dv-dim");
      if (online === false && p.mac && p.subnet && stationData?.subnet && p.subnet === stationData.subnet) {
        const wake = el("button", "dv-btn", "Wake (LAN)");
        wake.addEventListener("click", async () => {
          wake.disabled = true;
          const res = await fetch("/api/peer/wake", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ peer_id: p.peer_id }) }).then((r) => r.json()).catch(() => null);
          if (res && res.ok) { window.toast?.success?.("Wake packet sent"); } else { window.toast?.error?.(res?.error || "Wake failed"); }
          wake.disabled = false;
        });
        row.append(wake);
      }
      const manage = el("a", "dv-link", "Manage");
      manage.href = "#collab";
      const rename = el("a", "dv-link", "Rename");
      rename.href = "#";
      rename.addEventListener("click", (e) => {
        e.preventDefault();
        const input = el("input", "dv-input");
        input.type = "text"; input.value = p.alias || ""; input.placeholder = hostText || "name"; input.autocomplete = "off";
        const save = el("button", "dv-btn primary", "Save");
        const cancel = el("button", "dv-btn", "Cancel");
        const doSave = async () => {
          const res = await fetch("/api/peers/alias", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ peer_id: p.peer_id, alias: input.value }) }).then((r) => r.json()).catch(() => null);
          if (res && !res.error) { await load(); } else { window.toast?.error?.(res?.error || "Rename failed"); }
        };
        input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); doSave(); } else if (ev.key === "Escape") { ev.preventDefault(); renderAll(); } });
        save.addEventListener("click", doSave);
        cancel.addEventListener("click", () => renderAll());
        nameLine.textContent = "";
        nameLine.append(input, save, cancel);
      });
      sub.append(manage, document.createTextNode(" · "), rename);
      row.append(sub);
      list.append(row);
    }
    card.append(list);
  } else if (!myLinkInfo && !linkFormOpen) {
    card.append(el("div", "dv-empty", "No linked stations yet. Link another Mac, a Mac mini or a home server to work as one fleet."));
  }
  return card;
}

async function refreshVms() {
  const r = await fetch("/api/vms").then((x) => x.json()).catch(() => null);
  vmsData = Array.isArray(r?.vms) ? r.vms : [];
  renderAll();
}

function renderVms() {
  const card = el("section", "dv-card");
  card.append(el("h3", "dv-title", "Virtual machines"));
  if (!vmsData.length) {
    card.append(el("div", "dv-dim", "No virtual machines found."));
  }
  for (const v of vmsData) {
    const row = el("div", "dv-row");
    row.append(el("div", "", v.name || ""));
    row.append(el("span", "dv-rel", v.backend || ""));
    row.append(el("span", "dv-dim", " " + (v.status || "")));
    for (const act of ["start", "stop"]) {
      const b = el("button", "dv-btn", act === "start" ? "Start" : "Stop");
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          await post("/api/vms/action", { action: act, name: v.name, backend: v.backend });
        } catch (e) {
          window.toast?.error?.(e.message || "VM action failed");
        }
        await refreshVms();
      });
      row.append(b);
    }
    const linkBtn = el("button", "dv-btn", v.linked ? "Unlink" : "Link");
    linkBtn.addEventListener("click", async () => {
      linkBtn.disabled = true;
      try {
        await post("/api/vms/action", {
          action: v.linked ? "unlink" : "link",
          name: v.name,
          backend: v.backend,
          bootstrap_screen: true,
        });
      } catch (e) {
        window.toast?.error?.(e.message || "VM link action failed");
      }
      await load();
    });
    row.append(linkBtn);
    card.append(row);
  }
  const createRow = el("div", "dv-row");
  const nameInput = el("input", "dv-input");
  nameInput.placeholder = "subway-vm";
  nameInput.value = "subway-vm";
  const backendSel = el("select", "dv-input");
  for (const [val, label] of [
    ["lima", "Lima (Linux VM)"],
    ["devcontainer", "Docker / OrbStack"],
    ["utm", "UTM"],
    ["qemu", "QEMU"],
  ]) {
    const opt = el("option", "", label);
    opt.value = val;
    backendSel.append(opt);
  }
  const createBtn = el("button", "dv-btn", "Create VM");
  createBtn.addEventListener("click", async () => {
    createBtn.disabled = true;
    try {
      await post("/api/vms/action", {
        action: "create",
        name: nameInput.value.trim() || "subway-vm",
        backend: backendSel.value,
      });
    } catch (e) {
      window.toast?.error?.(e.message || "VM create failed");
    }
    await refreshVms();
  });
  createRow.append(nameInput, backendSel, createBtn);
  card.append(createRow);
  return card;
}

function renderAll() {
  if (!wrap) return;
  clearTicks();
  wrap.textContent = "";
  if (stationData && pairData) {
    wrap.append(renderMac(), renderFleetScreens(), renderLinkedStations(), renderVms(), renderRequests(), renderPaired());
  } else {
    const card = el("section", "dv-card dv-empty");
    card.append(el("span", "", "Couldn't load devices."));
    const retry = el("button", "dv-btn", "Retry");
    retry.addEventListener("click", load);
    card.append(retry);
    wrap.append(card);
  }
  // one shared 1s ticker for all "expires in" countdowns
  clearInterval(tickTimer);
  tickTimer = tickers.size ? setInterval(() => tickers.forEach((f) => f()), 1000) : 0;
}
let tickTimer = 0;

async function load() {
  try {
    const [s, p, pr, vmr] = await Promise.all([
      fetch("/api/station").then((r) => r.json()),
      fetch("/api/pair/list").then((r) => r.json()),
      fetch("/api/peers").then((r) => r.json()).catch(() => ({ peers: [] })),
      fetch("/api/vms").then((r) => r.json()).catch(() => ({ vms: [] })),
    ]);
    stationData = s; pairData = p; peersData = pr;
    vmsData = Array.isArray(vmr?.vms) ? vmr.vms : [];
    renderAll();
    clearInterval(activityTimer);
    if ((pr?.peers || []).length) {
      refreshActivity();
      activityTimer = setInterval(refreshActivity, ACTIVITY_POLL_MS);
      fetch("/api/peers/models").then((r) => r.json()).then((m) => {
        peerModels = {};
        for (const st of (m.stations || [])) if (st) for (const k of [st.station, st.peer_id]) if (k) peerModels[k] = st;
        renderAll();
      }).catch(() => { peerModels = {}; renderAll(); });
      fetch("/api/peers/mcp").then((r) => r.ok ? r.json() : { peers: [] }).then((m) => {
        peerMcps = {};
        for (const st of (m?.peers || [])) if (st) for (const k of [st.peer_id, st.station]) if (k) peerMcps[k] = st;
        renderAll();
      }).catch(() => { peerMcps = {}; renderAll(); });
    }
  } catch (e) {
    stationData = stationData || null;
    pairData = pairData || null;
    if (!wrap) return;
    clearTicks();
    wrap.textContent = "";
    const card = el("section", "dv-card dv-empty");
    card.append(el("span", "", `Couldn't load devices — ${e.message || e}`));
    const retry = el("button", "dv-btn", "Retry");
    retry.addEventListener("click", load);
    card.append(retry);
    wrap.append(card);
  }
}

let onPush = null;
function unmount() {
  clearInterval(timer); clearInterval(tickTimer); tickTimer = 0;
  clearInterval(activityTimer); activityTimer = 0;
  if (onPush) { window.removeEventListener("subway:push", onPush); onPush = null; }
  try { closeViewerNow(); } catch (_) {}
  clearTicks();
  wrap?.remove(); wrap = null;
  document.body.classList.remove("on-devices");
}
function mount() {
  unmount();
  const [r] = (location.hash.slice(1) || "home").split("/");
  if (r !== ROUTE) return;
  document.body.classList.add("on-devices");
  if (!document.getElementById("dv-css")) document.head.append(Object.assign(document.createElement("style"), { id: "dv-css", textContent: CSS }));
  wrap = document.createElement("div"); wrap.className = "dv-wrap";
  document.getElementById("page").append(wrap);
  load(); onPush = () => { if (wrap?.isConnected && !document.hidden) load(); }; window.addEventListener("subway:push", onPush);
}

document.addEventListener("page:render", mount);
mount();
