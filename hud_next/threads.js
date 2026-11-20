/* Global Lineage & Work-Graph Atlas (Canvas-First) + Timeline view for #threads. */
import { h, api, toast } from "/hud/lib.js";
import { shadowHost, hudSheet } from "./usage.js";

const ROUTE = "threads";
const REFRESH_MS = 5000;
const LIFECYCLES = ["working", "waiting", "monitoring", "snoozed", "settled"];
const LIFECYCLE_LABEL = {
  working: "Working",
  waiting: "Waiting",
  monitoring: "Monitoring",
  snoozed: "Snoozed",
  settled: "Settled",
};
const EDGE_LABEL = {
  spawned: "spawned",
  forked: "forked",
  handed_off: "handed off",
};

let wrap = null;
let root = null;
let box = null;
let timer = 0;
let lastSig = "";

const state = {
  scope: "subway",
  view: "canvas",
  query: "",
  zoom: 1,
  panX: 24,
  panY: 24,
  threads: [],
  history: [],
  workers: [],
  error: "",
  loading: false,
  loadedAt: 0,
  selectedId: "",
  hoverId: "",
  pinnedLocal: new Set(),
  settledLocal: new Set(),
};

const CSS = `
:host{display:block;color:var(--ink);font-family:var(--font-sans,system-ui,-apple-system,sans-serif)}
.atlas-shell{display:flex;flex-direction:column;gap:12px;min-width:0}
.atlas-toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:12px 14px;background:var(--surface);border:1px solid var(--hair);border-radius:var(--radius)}
.atlas-title-group{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;min-width:0}
.atlas-title{margin:0;font-size:16px;font-weight:650;letter-spacing:-.01em;color:var(--ink)}
.atlas-count{font-size:12.5px;color:var(--muted);font-variant-numeric:tabular-nums}
.atlas-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-left:auto}
.seg{display:inline-flex;gap:3px;padding:3px;border-radius:10px;background:var(--ground);border:1px solid var(--hair)}
.seg button{border:0;background:transparent;padding:5px 10px;border-radius:7px;font:inherit;font-size:12.5px;font-weight:500;color:var(--muted);cursor:pointer;white-space:nowrap}
.seg button:hover{color:var(--ink)}
.seg button[aria-pressed="true"]{background:var(--sky-chip-hi);color:var(--sky-ink);font-weight:600}
.atlas-search{min-width:170px;max-width:240px;padding:6px 10px;border-radius:8px;border:1px solid var(--hair);background:var(--ground);color:var(--ink);font:inherit;font-size:12.5px}
.atlas-search:focus{outline:2px solid var(--ochre);outline-offset:1px}
.zoom-bar{display:inline-flex;align-items:center;gap:4px;padding:3px;border-radius:10px;background:var(--ground);border:1px solid var(--hair)}
.btn{border:1px solid var(--hair);background:var(--surface);color:var(--ink);border-radius:8px;padding:5px 9px;font:inherit;font-size:12.5px;font-weight:500;cursor:pointer;white-space:nowrap}
.btn:hover{background:var(--cloud)}
.btn.primary{background:var(--sky-chip-hi);color:var(--sky-ink);border-color:transparent;font-weight:600}
.btn:focus-visible,.seg button:focus-visible{outline:2px solid var(--ochre);outline-offset:2px}
.zoom-pct{min-width:44px;text-align:center;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--muted)}
.atlas-legend{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);padding:0 4px}
.legend-item{display:inline-flex;align-items:center;gap:5px}
.dot{width:8px;height:8px;border-radius:99px;display:inline-block;background:var(--muted)}
.dot.working{background:var(--accent,#2563eb)}
.dot.waiting{background:var(--ochre,#d97706)}
.dot.monitoring{background:#0ea5e9}
.dot.snoozed{background:#8b5cf6}
.dot.settled{background:#64748b}
.atlas-stage{position:relative;height:min(72vh,740px);min-height:420px;overflow:hidden;border:1px solid var(--hair);border-radius:var(--radius);background-color:var(--surface);background-image:linear-gradient(to right,var(--hair) 1px,transparent 1px),linear-gradient(to bottom,var(--hair) 1px,transparent 1px);background-size:28px 28px;cursor:grab;user-select:none;touch-action:none}
.atlas-stage.dragging{cursor:grabbing}
.atlas-world{position:absolute;left:0;top:0;transform-origin:0 0;will-change:transform}
.atlas-edges{position:absolute;left:0;top:0;overflow:visible;pointer-events:none}
.edge-path{fill:none;stroke:var(--hair);stroke-width:1.75;transition:opacity .12s,stroke .12s,stroke-width .12s}
.edge-path.forked{stroke-dasharray:6 4}
.edge-path.handed_off{stroke-dasharray:2 4}
.edge-path.is-hi{stroke:var(--accent,#2563eb);stroke-width:2.5;opacity:1}
.edge-path.is-dim{opacity:.18}
.atlas-node{position:absolute;width:214px;padding:10px 12px;border-radius:12px;border:1px solid var(--hair);background:var(--surface);box-shadow:0 2px 8px rgba(0,0,0,.05);cursor:pointer;display:flex;flex-direction:column;gap:5px;transition:opacity .12s,border-color .12s,box-shadow .12s}
.atlas-node:hover,.atlas-node.is-hi{border-color:var(--accent,#2563eb);box-shadow:0 6px 18px rgba(0,0,0,.1)}
.atlas-node.is-selected{border-color:var(--ochre,#d97706);box-shadow:0 0 0 2px color-mix(in srgb,var(--ochre,#d97706) 28%,transparent)}
.atlas-node.is-dim{opacity:.24}
.atlas-node.worker-node{width:194px;border-style:dashed;background:var(--ground)}
.atlas-node-head{display:flex;align-items:center;justify-content:space-between;gap:6px}
.node-kind{font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.life-pill{display:inline-flex;align-items:center;gap:4px;font-size:10.5px;font-weight:600;padding:2px 7px;border-radius:99px;border:1px solid var(--hair);background:var(--ground);color:var(--ink)}
.life-pill.working{background:var(--sky-chip);color:var(--sky-ink);border-color:transparent}
.node-title{font-size:13px;font-weight:600;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.node-meta{font-size:11.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.quick-peek{position:absolute;right:14px;bottom:14px;width:min(340px,calc(100% - 28px));background:var(--surface);border:1px solid var(--hair);border-radius:var(--radius);padding:14px;box-shadow:0 14px 32px rgba(0,0,0,.16);display:flex;flex-direction:column;gap:10px;z-index:5;cursor:default}
.qp-head{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
.qp-title{font-size:14.5px;font-weight:650;color:var(--ink);overflow-wrap:anywhere}
.qp-close{border:0;background:transparent;color:var(--muted);font-size:16px;line-height:1;cursor:pointer;padding:2px 6px;border-radius:6px}
.qp-close:hover{background:var(--cloud);color:var(--ink)}
.qp-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.qp-kv{background:var(--ground);border:1px solid var(--hair);border-radius:8px;padding:7px 9px;min-width:0}
.qp-k{font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.qp-v{font-size:12.5px;font-weight:600;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.qp-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.timeline-list{display:flex;flex-direction:column;gap:10px}
.tl-item{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:12px 14px;background:var(--surface);border:1px solid var(--hair);border-radius:var(--radius)}
.tl-main{display:flex;flex-direction:column;gap:4px;min-width:0;flex:1 1 260px}
.tl-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tl-time{font-size:11.5px;color:var(--muted);font-variant-numeric:tabular-nums}
.tl-title{font-size:14px;font-weight:600;color:var(--ink);overflow-wrap:anywhere}
.tl-sub{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.empty-box{padding:32px;border:1px dashed var(--hair);border-radius:var(--radius);color:var(--muted);text-align:center;background:var(--surface)}
.err{padding:8px 12px;border-radius:8px;background:color-mix(in srgb,var(--ochre) 14%,var(--surface));border:1px solid var(--ochre);color:var(--ink);font-size:13px}
@media (max-width:47.99rem){
  .atlas-controls{margin-left:0;width:100%}
  .atlas-search{flex:1 1 140px;max-width:none}
  .atlas-stage{height:62vh;min-height:340px}
}
`;

function normLifecycle(raw, item = {}) {
  if (item.settled || item.archived || state.settledLocal.has(item.id)) return "settled";
  const s = String(raw || "").toLowerCase();
  if (s === "working" || s === "running" || s === "active" || s === "in_progress") return "working";
  if (s === "monitoring" || s === "watching" || s === "observing") return "monitoring";
  if (s === "snoozed" || s === "sleeping" || s === "muted") return "snoozed";
  if (s === "settled" || s === "done" || s === "completed" || s === "closed" || s === "archived" || s === "failed" || s === "stopped" || s === "killed") return "settled";
  return "waiting";
}

function tokensOf(item) {
  if (!item || typeof item !== "object") return 0;
  if (typeof item.tokens === "number") return item.tokens;
  if (typeof item.total_tokens === "number") return item.total_tokens;
  if (item.usage && typeof item.usage === "object") {
    return (Number(item.usage.input) || 0) + (Number(item.usage.output) || 0) + (Number(item.usage.total) || 0);
  }
  return 0;
}

function fmtTokens(n) {
  if (!n) return "0";
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function fmtTime(ts) {
  if (!ts) return "—";
  const ms = typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(ts);
  if (!Number.isFinite(ms)) return String(ts);
  return new Date(ms).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function tsMs(ts) {
  if (!ts) return 0;
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  const p = Date.parse(ts);
  return Number.isFinite(p) ? p : 0;
}

function buildGraph() {
  const nodes = new Map();
  const edges = [];
  const edgeKeys = new Set();

  const addEdge = (from, to, kind = "spawned") => {
    if (!from || !to || from === to) return;
    const k = `${from}|${to}|${kind}`;
    if (edgeKeys.has(k)) return;
    edgeKeys.add(k);
    edges.push({ from, to, kind });
  };

  for (const t of state.threads) {
    if (!t || typeof t !== "object") continue;
    const id = String(t.id || t.thread_id || t.chat_id || "");
    if (!id) continue;
    const meta = t.meta || {};
    const node = {
      id,
      chatId: id,
      kind: "thread",
      title: String(t.title || t.name || t.topic || id),
      project: String(t.project || t.folder || t.workspace || t.cwd || meta.project || ""),
      provider: String(t.provider || t.source || t.agent || t.line || meta.provider || "—"),
      lifecycle: normLifecycle(t.lifecycle || t.state || t.status, t),
      tokens: tokensOf(t),
      station: String(t.station || t.station_name || t.host || meta.station || "local"),
      principal: String(t.principal || t.principal_id || meta.principal || "—"),
      pinned: Boolean(t.pinned || meta.pinned || state.pinnedLocal.has(id)),
      ts: tsMs(t.updated_at || t.updated || t.timestamp || t.ts || t.created_at),
    };
    nodes.set(id, node);

    const parent = t.forked_from || t.forked_from_id || t.parent_id || t.parent_thread;
    if (parent) addEdge(String(parent), id, t.forked_from || t.forked_from_id ? "forked" : "spawned");
    if (t.handed_off_from || t.handoff_from) addEdge(String(t.handed_off_from || t.handoff_from), id, "handed_off");
    if (t.handed_off_to || t.handoff_to) addEdge(id, String(t.handed_off_to || t.handoff_to), "handed_off");
    if (Array.isArray(t.edges)) {
      for (const e of t.edges) {
        if (e && (e.from || e.source) && (e.to || e.target)) {
          addEdge(String(e.from || e.source), String(e.to || e.target), String(e.kind || e.type || e.relation || "spawned"));
        }
      }
    }
    if (Array.isArray(t.workers)) {
      for (const w of t.workers) {
        if (!w || typeof w !== "object") continue;
        const wid = String(w.id || w.worker_id || "");
        if (!wid) continue;
        if (!nodes.has(wid)) {
          nodes.set(wid, {
            id: wid,
            chatId: id,
            kind: "worker",
            title: String(w.role || w.title || w.summary || w.name || wid),
            project: node.project,
            provider: String(w.provider || w.model || node.provider),
            lifecycle: normLifecycle(w.status || w.state, w),
            tokens: tokensOf(w),
            station: String(w.station || node.station),
            principal: String(w.principal || node.principal),
            pinned: false,
            ts: tsMs(w.updated_at || w.finished_at || w.created_at || node.ts),
          });
        }
        const fromId = w.parent_id && nodes.has(String(w.parent_id)) ? String(w.parent_id) : id;
        addEdge(fromId, wid, w.forked_from ? "forked" : w.handed_off_to ? "handed_off" : "spawned");
      }
    }
  }

  for (const w of [...state.workers, ...state.history]) {
    if (!w || typeof w !== "object") continue;
    const wid = String(w.id || w.worker_id || "");
    if (!wid) continue;
    const parentId = String(w.thread_root || w.thread_id || w.chat_id || w.parent_id || "");
    if (!nodes.has(wid)) {
      nodes.set(wid, {
        id: wid,
        chatId: parentId || wid,
        kind: "worker",
        title: String(w.role || w.title || w.name || wid),
        project: String(w.project || w.workspace || ""),
        provider: String(w.provider || w.model || "—"),
        lifecycle: normLifecycle(w.status || w.state, w),
        tokens: tokensOf(w),
        station: String(w.station || w.host || "local"),
        principal: String(w.principal || "—"),
        pinned: false,
        ts: tsMs(w.updated_at || w.finished_at || w.created_at),
      });
    }
    if (parentId) addEdge(parentId, wid, w.forked_from ? "forked" : w.handed_off_to ? "handed_off" : "spawned");
  }

  const q = state.query.trim().toLowerCase();
  const matchesQuery = (n) => !q || [n.title, n.project, n.provider, n.id, n.station, n.principal].some((v) => String(v || "").toLowerCase().includes(q));
  const allNodes = [...nodes.values()];
  const visibleIds = new Set(allNodes.filter(matchesQuery).map((n) => n.id));
  if (q) {
    for (const e of edges) {
      if (visibleIds.has(e.from) || visibleIds.has(e.to)) {
        visibleIds.add(e.from);
        visibleIds.add(e.to);
      }
    }
  }
  const visibleNodes = allNodes.filter((n) => visibleIds.has(n.id));
  const visibleEdges = edges.filter((e) => visibleIds.has(e.from) && visibleIds.has(e.to));

  return { nodes: visibleNodes, edges: visibleEdges, totalThreads: state.threads.length, totalNodes: allNodes.length };
}

function layoutGraph(nodes, edges) {
  const pos = new Map();
  const children = new Map();
  const hasParent = new Set();
  for (const e of edges) {
    if (!children.has(e.from)) children.set(e.from, []);
    children.get(e.from).push(e.to);
    hasParent.add(e.to);
  }
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const roots = nodes.filter((n) => n.kind === "thread" || !hasParent.has(n.id));

  let laneY = 24;
  for (const life of LIFECYCLES) {
    const group = roots.filter((r) => r.lifecycle === life);
    if (!group.length) continue;
    for (const r of group) {
      if (pos.has(r.id)) continue;
      const queue = [{ id: r.id, depth: 0 }];
      const depthRow = new Map();
      let maxRows = 1;
      const seen = new Set([r.id]);
      while (queue.length) {
        const cur = queue.shift();
        const row = depthRow.get(cur.depth) || 0;
        depthRow.set(cur.depth, row + 1);
        maxRows = Math.max(maxRows, row + 1);
        pos.set(cur.id, { x: 24 + cur.depth * 268, y: laneY + row * 108 });
        for (const cid of children.get(cur.id) || []) {
          if (!seen.has(cid) && nodeById.has(cid)) {
            seen.add(cid);
            queue.push({ id: cid, depth: cur.depth + 1 });
          }
        }
      }
      laneY += maxRows * 108 + 26;
    }
  }
  for (const n of nodes) {
    if (!pos.has(n.id)) {
      pos.set(n.id, { x: 24, y: laneY });
      laneY += 108;
    }
  }
  return pos;
}

function connectedSet(nodeId, edges) {
  if (!nodeId) return null;
  const adj = new Map();
  for (const e of edges) {
    if (!adj.has(e.from)) adj.set(e.from, []);
    if (!adj.has(e.to)) adj.set(e.to, []);
    adj.get(e.from).push(e.to);
    adj.get(e.to).push(e.from);
  }
  const seen = new Set([nodeId]);
  const q = [nodeId];
  while (q.length) {
    const cur = q.shift();
    for (const nxt of adj.get(cur) || []) {
      if (!seen.has(nxt)) {
        seen.add(nxt);
        q.push(nxt);
      }
    }
  }
  return seen;
}

function openInHome(chatId) {
  const id = String(chatId || "");
  location.hash = id ? `#home/${encodeURIComponent(id)}` : "#home";
}

async function togglePin(node) {
  if (state.pinnedLocal.has(node.id)) state.pinnedLocal.delete(node.id);
  else state.pinnedLocal.add(node.id);
  paint();
  try {
    await api(`/api/threads/${encodeURIComponent(node.id)}/pin`, { pinned: state.pinnedLocal.has(node.id) });
  } catch (_) { /* optimistic state stays */ }
}

async function settleThread(node) {
  state.settledLocal.add(node.id);
  paint();
  try {
    await api(`/api/threads/${encodeURIComponent(node.id)}/settle`, { settled: true });
  } catch (_) {
    toast("Settled locally");
  }
}

async function loadData(force = false) {
  if (state.loading || (!force && Date.now() - state.loadedAt < 4000)) return false;
  state.loading = true;
  try {
    const scope = state.scope || "subway";
    const [tRes, hRes, sRes] = await Promise.allSettled([
      api(`/api/threads?scope=${encodeURIComponent(scope)}&limit=200`),
      api("/api/history?limit=100"),
      api("/api/status"),
    ]);
    if (tRes.status === "fulfilled" && tRes.value) {
      const d = tRes.value;
      state.threads = d.threads || d.chats || (Array.isArray(d) ? d : []);
      state.error = "";
    } else if (tRes.status === "rejected") {
      state.error = String(tRes.reason || "Failed to load threads");
    }
    if (hRes.status === "fulfilled" && hRes.value) {
      const hd = hRes.value;
      state.history = hd.history || hd.workers || (Array.isArray(hd) ? hd : []);
    }
    if (sRes.status === "fulfilled" && sRes.value) {
      state.workers = sRes.value.workers || [];
    }
  } finally {
    state.loading = false;
    state.loadedAt = Date.now();
  }
  return true;
}

function fitToStage(stage, pos) {
  if (!stage || !pos.size) return;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of pos.values()) {
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + 220);
    maxY = Math.max(maxY, p.y + 96);
  }
  const rect = stage.getBoundingClientRect();
  const w = Math.max(300, rect.width || 800);
  const hgt = Math.max(260, rect.height || 500);
  const scale = Math.max(0.3, Math.min(1.25, Math.min((w - 40) / (maxX - minX), (hgt - 40) / (maxY - minY))));
  state.zoom = Number(scale.toFixed(2));
  state.panX = Math.round(20 - minX * state.zoom);
  state.panY = Math.round(20 - minY * state.zoom);
}

function renderQuickPeek(node) {
  if (!node) return null;
  const pinned = node.pinned || state.pinnedLocal.has(node.id);
  return h("div", { class: "quick-peek", onpointerdown: (e) => e.stopPropagation() },
    h("div", { class: "qp-head" },
      h("div", null,
        h("div", { class: "node-kind" }, `${node.kind === "worker" ? "Worker" : "Thread"} · ${LIFECYCLE_LABEL[node.lifecycle] || node.lifecycle}`),
        h("div", { class: "qp-title" }, node.title)
      ),
      h("button", { class: "qp-close", type: "button", "aria-label": "Close Quick-Peek", onclick: () => { state.selectedId = ""; paint(); } }, "×")
    ),
    h("div", { class: "qp-grid" },
      h("div", { class: "qp-kv" }, h("div", { class: "qp-k" }, "Provider"), h("div", { class: "qp-v" }, node.provider || "—")),
      h("div", { class: "qp-kv" }, h("div", { class: "qp-k" }, "Tokens"), h("div", { class: "qp-v" }, fmtTokens(node.tokens))),
      h("div", { class: "qp-kv" }, h("div", { class: "qp-k" }, "Station"), h("div", { class: "qp-v" }, node.station || "local")),
      h("div", { class: "qp-kv" }, h("div", { class: "qp-k" }, "Principal"), h("div", { class: "qp-v" }, node.principal || "—"))
    ),
    h("div", { class: "qp-actions" },
      h("button", { class: "btn primary", type: "button", onclick: () => openInHome(node.chatId || node.id) }, "Open in Home"),
      h("button", { class: "btn", type: "button", onclick: () => togglePin(node) }, pinned ? "Unpin" : "Pin"),
      h("button", { class: "btn", type: "button", onclick: () => settleThread(node) }, "Settle")
    )
  );
}

function renderCanvas(graph) {
  const { nodes, edges } = graph;
  if (!nodes.length) return h("div", { class: "empty-box" }, state.query ? "No matching threads or workers." : "No threads in this scope yet.");
  const pos = layoutGraph(nodes, edges);
  const hiSet = connectedSet(state.hoverId || state.selectedId, edges);
  const selectedNode = nodes.find((n) => n.id === state.selectedId) || null;

  const world = h("div", { class: "atlas-world", style: `transform:translate(${state.panX}px,${state.panY}px) scale(${state.zoom})` });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "atlas-edges");
  svg.setAttribute("width", "4000");
  svg.setAttribute("height", "4000");

  for (const e of edges) {
    const a = pos.get(e.from);
    const b = pos.get(e.to);
    if (!a || !b) continue;
    const x1 = a.x + 214, y1 = a.y + 42;
    const x2 = b.x, y2 = b.y + 42;
    const dx = Math.max(40, Math.abs(x2 - x1) * 0.5);
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const isHi = hiSet && hiSet.has(e.from) && hiSet.has(e.to);
    const cls = ["edge-path", e.kind || "spawned", hiSet ? (isHi ? "is-hi" : "is-dim") : ""].filter(Boolean).join(" ");
    p.setAttribute("class", cls);
    p.setAttribute("d", `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`);
    p.setAttribute("aria-label", EDGE_LABEL[e.kind] || e.kind);
    svg.appendChild(p);
  }
  world.appendChild(svg);

  for (const n of nodes) {
    const p = pos.get(n.id);
    if (!p) continue;
    const isHi = hiSet && hiSet.has(n.id);
    const cls = [
      "atlas-node",
      n.kind === "worker" ? "worker-node" : "",
      state.selectedId === n.id ? "is-selected" : "",
      hiSet ? (isHi ? "is-hi" : "is-dim") : "",
    ].filter(Boolean).join(" ");
    const el = h("div", {
      class: cls,
      style: `left:${p.x}px;top:${p.y}px`,
      onpointerdown: (e) => e.stopPropagation(),
      onmouseenter: () => { if (state.hoverId !== n.id) { state.hoverId = n.id; paint(); } },
      onmouseleave: () => { if (state.hoverId === n.id) { state.hoverId = ""; paint(); } },
      onclick: (e) => { e.stopPropagation(); state.selectedId = state.selectedId === n.id ? "" : n.id; paint(); },
    },
      h("div", { class: "atlas-node-head" },
        h("span", { class: "node-kind" }, n.kind === "worker" ? "Worker" : "Thread"),
        h("span", { class: `life-pill ${n.lifecycle}` }, h("span", { class: `dot ${n.lifecycle}` }), LIFECYCLE_LABEL[n.lifecycle] || n.lifecycle)
      ),
      h("div", { class: "node-title", title: n.title }, n.title),
      h("div", { class: "node-meta" }, `${n.provider} · ${fmtTokens(n.tokens)} tok · ${n.station}`)
    );
    world.appendChild(el);
  }

  const stage = h("div", {
    class: "atlas-stage",
    onwheel: (e) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.1 : 0.9;
      state.zoom = Math.max(0.25, Math.min(2.5, Number((state.zoom * factor).toFixed(2))));
      paint();
    },
    onpointerdown: (e) => {
      if (e.button !== 0) return;
      stage.classList.add("dragging");
      const sx = e.clientX - state.panX;
      const sy = e.clientY - state.panY;
      const onMove = (ev) => {
        state.panX = Math.round(ev.clientX - sx);
        state.panY = Math.round(ev.clientY - sy);
        world.style.transform = `translate(${state.panX}px,${state.panY}px) scale(${state.zoom})`;
      };
      const onUp = () => {
        stage.classList.remove("dragging");
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
  }, world, renderQuickPeek(selectedNode));

  stage._fit = () => { fitToStage(stage, pos); paint(); };
  return stage;
}

function renderTimeline(graph) {
  const { nodes, edges } = graph;
  if (!nodes.length) return h("div", { class: "empty-box" }, "No timeline events in this scope.");
  const edgeByTarget = new Map(edges.map((e) => [e.to, e]));
  const sorted = [...nodes].sort((a, b) => b.ts - a.ts);
  return h("div", { class: "timeline-list" },
    ...sorted.map((n) => {
      const edge = edgeByTarget.get(n.id);
      const badge = edge ? (EDGE_LABEL[edge.kind] || edge.kind) : (n.kind === "worker" ? "worker run" : "thread root");
      return h("div", { class: "tl-item" },
        h("div", { class: "tl-main" },
          h("div", { class: "tl-top" },
            h("span", { class: `life-pill ${n.lifecycle}` }, h("span", { class: `dot ${n.lifecycle}` }), LIFECYCLE_LABEL[n.lifecycle] || n.lifecycle),
            h("span", { class: "node-kind" }, badge),
            h("span", { class: "tl-time" }, fmtTime(n.ts))
          ),
          h("div", { class: "tl-title" }, n.title),
          h("div", { class: "tl-sub" }, `${n.project || "workspace"} · ${n.station} · ${n.provider} · principal ${n.principal} · ${fmtTokens(n.tokens)} tok`)
        ),
        h("button", { class: "btn primary", type: "button", onclick: () => openInHome(n.chatId || n.id) }, "Open in Home")
      );
    })
  );
}

function paint() {
  if (!wrap || !box) return;
  const graph = buildGraph();
  const stageEl = state.view === "canvas" ? renderCanvas(graph) : renderTimeline(graph);

  const toolbar = h("div", { class: "atlas-toolbar" },
    h("div", { class: "atlas-title-group" },
      h("h2", { class: "atlas-title" }, "Thread Atlas"),
      h("span", { class: "atlas-count" }, `${graph.totalThreads} threads · ${graph.totalNodes} nodes`)
    ),
    h("div", { class: "atlas-controls" },
      h("div", { class: "seg", role: "group", "aria-label": "Scope" },
        h("button", { type: "button", "aria-pressed": String(state.scope === "subway"), onclick: () => { state.scope = "subway"; loadData(true).then(paint); } }, "Subway"),
        h("button", { type: "button", "aria-pressed": String(state.scope === "all"), onclick: () => { state.scope = "all"; loadData(true).then(paint); } }, "All")
      ),
      h("div", { class: "seg", role: "group", "aria-label": "View" },
        h("button", { type: "button", "aria-pressed": String(state.view === "canvas"), onclick: () => { state.view = "canvas"; paint(); } }, "Canvas"),
        h("button", { type: "button", "aria-pressed": String(state.view === "timeline"), onclick: () => { state.view = "timeline"; paint(); } }, "Timeline")
      ),
      h("input", {
        class: "atlas-search",
        type: "search",
        placeholder: "Filter title, project, provider, id…",
        value: state.query,
        oninput: (e) => { state.query = e.target.value; paint(); },
      }),
      state.view === "canvas" ? h("div", { class: "zoom-bar", role: "group", "aria-label": "Zoom controls" },
        h("button", { class: "btn", type: "button", "aria-label": "Zoom out", onclick: () => { state.zoom = Math.max(0.25, Number((state.zoom - 0.15).toFixed(2))); paint(); } }, "-"),
        h("span", { class: "zoom-pct" }, `${Math.round(state.zoom * 100)}%`),
        h("button", { class: "btn", type: "button", "aria-label": "Zoom in", onclick: () => { state.zoom = Math.min(2.5, Number((state.zoom + 0.15).toFixed(2))); paint(); } }, "+"),
        h("button", { class: "btn", type: "button", onclick: () => stageEl && stageEl._fit && stageEl._fit() }, "Fit"),
        h("button", { class: "btn", type: "button", onclick: () => { state.zoom = 1; state.panX = 24; state.panY = 24; paint(); } }, "100%")
      ) : null
    )
  );

  const legend = h("div", { class: "atlas-legend" },
    ...LIFECYCLES.map((l) => h("span", { class: "legend-item" }, h("span", { class: `dot ${l}` }), LIFECYCLE_LABEL[l])),
    h("span", { class: "legend-item" }, "Edges: spawned · forked · handed_off")
  );

  const parts = [toolbar, legend];
  if (state.error) parts.push(h("div", { class: "err" }, state.error));
  parts.push(stageEl);
  box.replaceChildren(...parts);
  lastSig = JSON.stringify({ t: state.threads.length, h: state.history.length, w: state.workers.length, s: state.scope, e: state.error });
}

async function tick() {
  clearTimeout(timer);
  if (!wrap) return;
  const fresh = await loadData(false);
  if (!wrap) return;
  const sig = JSON.stringify({ t: state.threads.length, h: state.history.length, w: state.workers.length, s: state.scope, e: state.error });
  if (fresh && sig !== lastSig) paint();
  timer = setTimeout(tick, REFRESH_MS);
}

function unmount() {
  clearTimeout(timer);
  if (!wrap) return;
  document.body.classList.remove("on-threads");
  wrap.remove();
  wrap = root = box = null;
}

async function mount() {
  const [r, sel] = (location.hash.slice(1) || "home").split("/");
  if (r !== ROUTE) { unmount(); return; }
  if (sel) state.selectedId = decodeURIComponent(sel);
  document.querySelector("#page .th-wrap")?.remove();
  if (wrap) { paint(); return; }
  document.body.classList.add("on-threads");
  const sh = shadowHost("us-hud th-hud");
  root = sh.root;
  const style = document.createElement("style");
  style.textContent = CSS;
  root.append(style);
  const myRoot = root;
  hudSheet().then(() => { if (root === myRoot) root.append(style); });
  box = h("div", { class: "atlas-shell" }, h("div", { class: "empty-box" }, "Loading Thread Atlas…"));
  root.append(box);
  wrap = h("div", { class: "us-wrap" }, sh.host);
  document.getElementById("page").append(wrap);
  await loadData(true);
  if (root !== myRoot) return;
  paint();
  timer = setTimeout(tick, REFRESH_MS);
}

document.addEventListener("page:render", mount);
mount();

export { ROUTE, mount, unmount };
