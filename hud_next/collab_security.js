import { api } from "/hud/lib.js";

const T = {
  audit: [],
  auditErr: "",
  auditReady: false,
  loading: false,
  rotate: null,
  rotArmed: false,
  rotPending: false,
  filter: "all",
};
let rerender = () => {};

const SEC_ACTIONS = {
  "pair.refused": ["Pairing refused", "warn"],
  "peer_rate_limit": ["Slowed down", "warn"],
  "peer.pk_rotated": ["changed its key", "info"],
  "peer.link": ["Linked", "info"],
  "peer.unlink": ["Removed", "info"],
};

function fingerprint(pk) {
  return String(pk || "").replace(/[^0-9a-f]/gi, "").slice(0, 8).match(/.{1,4}/g)?.join(" ") || "unknown";
}

function routeChip(p) {
  return p && p.lan ? "Local network" : "Relay · end-to-end encrypted";
}

function ago(ts) {
  if (!ts) return "never";
  const d = Date.now() / 1000 - Number(ts);
  if (!Number.isFinite(d) || d < 0) return "unknown";
  if (d < 60) return "just now";
  if (d < 3600) return Math.floor(d / 60) + "m ago";
  if (d < 86400) return Math.floor(d / 3600) + "h ago";
  return Math.floor(d / 86400) + "d ago";
}

function secEvents() {
  return (T.audit || [])
    .filter((e) => SEC_ACTIONS[e && e.action])
    .filter((e) => T.filter === "all" || SEC_ACTIONS[e.action][1] === T.filter)
    .sort((a, b) => Number(b && b.ts || 0) - Number(a && a.ts || 0))
    .slice(0, 50);
}

function stationsCard(peers, esc) {
  if (!peers || !peers.length) {
    return `<div class="cb-card sc-card"><div class="cb-h">Linked stations</div><div class="cb-muted">No linked stations yet. Link one from People.</div></div>`;
  }
  const rows = peers.map((p) => {
    const name = esc(p.name || p.alias || p.display_name || "Station");
    const rel = esc(p.relation || "partner");
    const fp = esc(fingerprint(p && p.station && p.station.pk));
    const ls = p.last_seen ? ago(p.last_seen) : "never";
    return `<div class="sc-st">
      <div><strong>${name}</strong> <span class="sc-chip">${rel}</span> <span class="sc-chip route">${esc(routeChip(p))}</span></div>
      <div class="sc-fp" title="Key fingerprint">${fp}</div>
      <div class="cb-muted">Last seen ${esc(ls)}</div>
    </div>`;
  }).join("");
  return `<div class="cb-card sc-card"><div class="cb-h">Linked stations</div>${rows}</div>`;
}

function rotateCard(esc) {
  let btnLabel = "Rotate keys";
  let btnAttr = "";
  if (T.rotPending) { btnLabel = "Rotating…"; btnAttr = "disabled"; }
  else if (T.rotArmed) { btnLabel = "Click again to confirm"; }
  let res = "";
  if (T.rotate) {
    if (T.rotate.unavailable) {
      res = `<div class="cb-muted">Not available on this build</div>`;
    } else if (Array.isArray(T.rotate.results)) {
      const items = T.rotate.results.map((r) => {
        const st = esc(r.station || "Station");
        if (r.ok) return `<li>${st} <span>ok</span></li>`;
        return `<li class="err">${st}: ${esc(r.error || "error")} — Re-link this station</li>`;
      }).join("");
      res = `<ul class="sc-res">${items}</ul>`;
    }
  }
  return `<div class="cb-card sc-card">
    <div class="cb-h">Rotate keys</div>
    <div class="cb-muted">Make a new encryption key for this station. Use it after you unlink someone you no longer trust. Linked stations are told the new key automatically. Offline ones need to be re-linked.</div>
    <div><button class="cb-btn danger" data-sec="rotate" ${btnAttr}>${esc(btnLabel)}</button></div>
    ${res}
  </div>`;
}

function eventsCard(esc) {
  const filters = ["all", "warn", "info"].map((f) => {
    const label = f === "all" ? "All" : f === "warn" ? "Warnings" : "Info";
    return `<button class="cb-seg ${T.filter === f ? "on" : ""}" data-sec-filter="${f}">${label}</button>`;
  }).join("");
  let body = "";
  if (T.auditErr) {
    body = `<div class="cb-muted">Couldn't read the activity log.</div>`;
  } else if (T.loading && !T.auditReady) {
    body = `<div class="cb-muted">Loading…</div>`;
  } else {
    const evs = secEvents();
    if (!evs.length) {
      body = `<div class="cb-muted">Nothing unusual. Refused pairings, rate limits and key changes will show here.</div>`;
    } else {
      body = evs.map((e) => {
        const meta = SEC_ACTIONS[e.action] || [e.action || "Event", "info"];
        const sev = meta[1];
        const label = esc(meta[0] + (e.peer || e.actor ? " " + (e.peer || e.actor) : ""));
        const who = esc(e.actor || e.peer || "");
        return `<div class="sc-ev"><span class="sc-dot ${sev}"></span><div><div>${label}</div><div class="cb-muted">${who} ${esc(ago(e.ts))}</div></div></div>`;
      }).join("");
    }
  }
  return `<div class="cb-card sc-card"><div class="cb-h">Recent security events</div><div class="cb-seg-row">${filters}</div>${body}</div>`;
}

function accessCard(peers, ctx) {
  if (!peers || !peers.length) return "";
  const rows = peers.map((p) => {
    const name = ctx.esc(p.name || p.alias || p.display_name || "Station");
    const preset = ctx.esc((ctx.activePreset && ctx.activePreset(p)) || "Custom");
    const g = p.grants || {};
    const chats = g.chats != null ? String(g.chats) : "—";
    const spawn = g.spawn != null ? String(g.spawn) : "—";
    const land = g.land != null ? String(g.land) : "—";
    const swarm = g.swarm ? "On" : "Off";
    return `<div class="pp-acc-row"><strong>${name}</strong><div class="cb-muted">Preset: ${preset}</div><div class="cb-muted">Chats: ${ctx.esc(chats)} · Spawn: ${ctx.esc(spawn)} · Land: ${ctx.esc(land)}</div><div class="cb-muted">Mode C: ${swarm} <span>(Change it on the People tab)</span></div></div>`;
  }).join("");
  return `<div class="cb-card sc-card"><div class="cb-h">Access summary</div>${rows}</div>`;
}

let bound = false;

async function onSecClick(e) {
  const root = e.target.closest("[data-sec-root]");
  if (!root) return;
  const fbtn = e.target.closest("[data-sec-filter]");
  if (fbtn) {
    T.filter = fbtn.getAttribute("data-sec-filter") || "all";
    rerender();
    return;
  }
  const btn = e.target.closest("[data-sec]");
  if (!btn) return;
  const action = btn.getAttribute("data-sec");
  if (action === "rotate") {
    if (T.rotPending) return;
    if (!T.rotArmed) {
      T.rotArmed = true;
      rerender();
      setTimeout(() => { T.rotArmed = false; rerender(); }, 5000);
      return;
    }
    T.rotArmed = false;
    T.rotPending = true;
    T.rotate = null;
    rerender();
    try {
      const r = await api("/api/peers/rotate_keys", { method: "POST" });
      T.rotate = r || { results: [] };
    } catch (err) {
      const msg = String(err && err.message || err);
      if (msg.includes("404")) T.rotate = { unavailable: true };
      else T.rotate = { results: [{ station: "Station", ok: false, error: msg }] };
    } finally {
      T.rotPending = false;
      rerender();
    }
  }
}

export function securityTab(ctx) {
  rerender = ctx.rerender;
  if (!bound) {
    bound = true;
    document.addEventListener("click", onSecClick);
  }
  if (!T.auditReady && !T.loading) {
    T.loading = true;
    api("/api/audit?limit=300")
      .then((r) => { T.audit = (r && r.entries) || []; })
      .catch((e) => { T.auditErr = String(e && e.message || e); })
      .finally(() => { T.auditReady = true; T.loading = false; rerender(); });
  }
  const peers = ctx.peers || [];
  return `<div class="sc-grid" data-sec-root>`
    + `<div class="sc-col">${stationsCard(peers, ctx.esc)}${eventsCard(ctx.esc)}</div>`
    + `<div class="sc-col">${rotateCard(ctx.esc)}${accessCard(peers, ctx)}</div>`
    + `</div>`;
}

export const SECCSS = `
.sc-grid{display:grid;gap:16px;padding:0;grid-template-columns:minmax(0,1fr)}
@media (min-width:1200px){.sc-grid{grid-template-columns:minmax(0,1.5fr) minmax(0,1fr)}}
.sc-col{display:grid;gap:16px;align-content:start;min-width:0}
.sc-card{padding:14px;display:grid;gap:12px;min-width:0}
.sc-st{display:grid;gap:6px;padding:10px 12px;border-radius:10px;background:var(--ground);min-width:0;overflow-wrap:anywhere}
.sc-fp{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:13px;letter-spacing:.04em;overflow-wrap:anywhere}
.sc-chip{font-size:11px;font-weight:600;padding:2px 8px;border-radius:99px;border:1px solid var(--hair, var(--line));color:var(--muted);display:inline-block;margin-right:4px}
.sc-chip.route{background:var(--sky-chip-hi, rgba(90,122,140,.12));color:var(--sky-ink, var(--accent));border-color:transparent}
.sc-res{list-style:none;margin:0;padding:0;display:grid;gap:6px;font-size:12.5px;overflow-wrap:anywhere}
.sc-res .err{color:#A23B2A}
.sc-ev{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-top:1px solid var(--hair, var(--line));min-width:0;overflow-wrap:anywhere}
.sc-dot{width:8px;height:8px;border-radius:50%;flex:none;margin-top:6px;background:var(--sky-shade, var(--muted))}
.sc-dot.warn{background:var(--ochre, #b7791f)}
.sc-dot.info{background:var(--muted)}
.cb-seg-row{display:flex;gap:6px;flex-wrap:wrap}
`;
