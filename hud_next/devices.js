// Devices page prototype — plain light DOM, mounts on route "devices".
const ROUTE = "devices";
let wrap = null, timer = 0;

const CSS = `
.dv-wrap{padding:24px;max-width:1200px;margin:0 auto}
.dv-grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}
.dv-card{background:#fff;border:1px solid var(--hair);border-radius:var(--radius);padding:20px;grid-column:span 12}
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
.dv-chip{font-size:11px;padding:3px 9px;border-radius:999px;background:var(--sky);color:var(--sky-ink)}
.dv-note{font-size:12px;color:var(--muted);margin-top:10px}
.dv-station{display:flex;align-items:center;gap:8px;margin-top:12px}
.dv-station code{font-family:"JetBrains Mono",monospace;font-size:12px;background:var(--hair);border-radius:8px;padding:5px 8px;overflow-wrap:anywhere}
.dv-iconbtn{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:8px;border:1px solid var(--hair);background:#fff;color:var(--muted);cursor:pointer}
.dv-iconbtn:hover{color:var(--ink)}
.dv-req{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--hair)}
.dv-req:last-child{border-bottom:none}
.dv-req .name{font-size:13px;font-weight:600;color:var(--ink)}
.dv-req .code{font-family:"JetBrains Mono",monospace;font-size:22px;letter-spacing:.08em;color:var(--ink)}
.dv-req .exp{font-size:12px;color:var(--muted)}
.dv-req .grow{flex:1 1 auto}
.dv-scope-sel{font-size:12px;padding:6px 8px;border-radius:10px;border:1px solid var(--hair);background:#fff;color:var(--ink)}
.dv-btn{font:inherit;font-size:12px;font-weight:600;padding:7px 14px;border-radius:8px;border:1px solid var(--hair);background:#fff;color:var(--ink);cursor:pointer}
.dv-btn:hover{background:var(--hair)}
.dv-btn.primary{background:var(--sky);border-color:transparent;color:var(--sky-ink)}
.dv-btn.primary:hover{filter:brightness(.97)}
.dv-btn.danger{color:#c2413a;background:#fdeceb;border-color:transparent}
.dv-btn.danger:hover{filter:brightness(.97)}
.dv-btn.ghost{border-color:transparent;background:transparent;color:var(--muted)}
.dv-btn.ghost:hover{color:var(--ink);background:var(--hair)}
.dv-btn:disabled{opacity:.5;cursor:default}
.dv-confirm{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:12px;color:var(--ink);background:var(--hair);border-radius:8px;padding:8px 10px}
.dv-empty{font-size:13px;color:var(--muted);padding:8px 0}
.dv-table{width:100%;border-collapse:collapse;font-size:13px}
.dv-table th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;padding:8px 10px;border-bottom:1px solid var(--hair)}
.dv-table td{padding:10px;border-bottom:1px solid var(--hair);color:var(--ink);vertical-align:middle}
.dv-table tr:last-child td{border-bottom:none}
.dv-dev{display:inline-flex;align-items:center;gap:8px}
.dv-dev svg{color:var(--muted)}
.dv-namebtn{font:inherit;font-size:13px;color:var(--ink);background:none;border:none;border-bottom:1px dashed transparent;cursor:pointer;padding:0}
.dv-namebtn:hover{border-bottom-color:var(--muted)}
.dv-rename{font:inherit;font-size:13px;border:1px solid var(--hair);border-radius:10px;padding:4px 8px;width:160px}
.dv-tag{display:inline-block;font-size:11px;font-weight:600;padding:3px 10px;border-radius:999px;background:var(--sky);color:var(--sky-ink)}
.dv-tag.full{background:#fdeceb;color:#c2413a}
.dv-tag.read{background:var(--hair);color:var(--muted)}
.dv-dim{color:var(--muted)}
.dv-details{margin-top:12px}
.dv-details summary{font-size:12px;color:var(--muted);cursor:pointer}
.dv-details .dv-table{opacity:.55}
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
  if (text != null) n.textContent = text;
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

// --- render helpers -------------------------------------------------------

let stationData = null, pairData = null;
const tickers = new Set();

function clearTicks() { tickers.clear(); }

function renderMac() {
  const card = el("section", "dv-card dv-mac");
  card.append(el("h2", "dv-h2", "This Mac"));
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
  card.append(el("div", "dv-note", "configured, not a live check"));
  // station id + copy
  const st = el("div", "dv-station");
  const code = el("code", "", stationData?.station_id || "—");
  st.append(code);
  const cp = iconBtn(ICONS.copy, "Copy station id");
  cp.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(stationData?.station_id || ""); window.toast?.success("Station id copied"); }
    catch { window.toast?.error("Copy failed", { description: "Clipboard unavailable" }); }
  });
  st.append(cp);
  card.append(st);
  return card;
}

function renderRequests() {
  const card = el("section", "dv-card dv-wait");
  card.append(el("h2", "dv-h2", "Waiting to pair"));
  const reqs = pairData?.requests || [];
  if (!reqs.length) {
    card.append(el("div", "dv-empty", "No devices waiting. Pair from the Subway app on your phone."));
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

function renderPaired() {
  const card = el("section", "dv-card dv-paired");
  card.append(el("h2", "dv-h2", "Paired devices"));
  const all = pairData?.devices || [];
  const active = all.filter((d) => !d.revoked).sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0));
  const revoked = all.filter((d) => d.revoked);
  if (!active.length) card.append(el("div", "dv-empty", "No paired devices yet."));
  else {
    const table = el("table", "dv-table");
    const thead = el("thead");
    const trh = el("tr");
    for (const h of ["Name", "Platform", "Scope", "Paired", "Last seen", ""]) trh.append(el("th", "", h));
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
  tr.append(nameTd, platTd, scopeTd,
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

function renderAll() {
  if (!wrap) return;
  clearTicks();
  wrap.textContent = "";
  if (stationData && pairData) {
    wrap.append(renderMac(), renderRequests(), renderPaired());
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
    const [s, p] = await Promise.all([
      fetch("/api/station").then((r) => r.json()),
      fetch("/api/pair/list").then((r) => r.json()),
    ]);
    stationData = s; pairData = p;
    renderAll();
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
  if (onPush) { window.removeEventListener("subway:push", onPush); onPush = null; }
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
