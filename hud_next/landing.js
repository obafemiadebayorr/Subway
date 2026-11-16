/* Landing page for hud_next (#landing): overlaps, landing queue, worker branches, and inline/side-by-side diff viewer. */
import { api } from "/hud/lib.js";
import { DIFF_CSS, renderDiffHtml } from "./diff.js";

const ROUTE = "landing";
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const say = (m, k = "info") => { try { (window.toast?.[k] || window.toast || console.log)(m); } catch (_) {} };

const CSS = `
.ld-wrap{display:flex;flex-direction:column;gap:16px;margin-top:16px;min-width:0}
.ld-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.ld-stat{background:var(--surface);border-radius:var(--radius);box-shadow:0 0 0 1px var(--hair);padding:14px 16px;display:flex;flex-direction:column;gap:2px}
.ld-stat b{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}
.ld-stat span{font-size:12.5px;color:var(--muted)}
.ld-card{background:var(--surface);border-radius:var(--radius);box-shadow:0 0 0 1px var(--hair);padding:16px;display:flex;flex-direction:column;gap:12px;min-width:0}
.ld-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;flex-wrap:wrap}
.ld-head h2{margin:0;font-size:16px;font-weight:650}
.ld-sub{font-size:12.5px;color:var(--muted);margin-top:2px}
.ld-rows{display:flex;flex-direction:column;gap:8px}
.ld-row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:12px;border-radius:10px;background:var(--ground)}
.ld-row-main{min-width:0;flex:1 1 260px}
.ld-title{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;font-weight:600;overflow-wrap:anywhere}
.ld-meta{font-size:12.5px;color:var(--muted);margin-top:3px;overflow-wrap:anywhere}
.ld-acts{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ld-tag{font-size:11px;font-weight:600;padding:3px 8px;border-radius:99px;background:var(--surface);box-shadow:0 0 0 1px var(--hair);color:var(--muted)}
.ld-tag.bad{color:#9C3F2B}.ld-tag.ok{color:#1A7F37}
.ld-btn{border:0;border-radius:8px;padding:7px 12px;font:inherit;font-size:12.5px;font-weight:500;cursor:pointer;background:var(--surface);box-shadow:0 0 0 1px var(--hair);color:var(--ink)}
.ld-btn.primary{background:var(--sky-chip-hi);color:var(--sky-ink);box-shadow:none}
.ld-btn.danger{color:#9C3F2B}
.ld-btn:disabled{opacity:.55;cursor:default}
.ld-empty{padding:18px;border:1px dashed var(--hair);border-radius:10px;color:var(--muted);text-align:center;font-size:13px}
.ld-diff-panel{background:var(--surface);border-radius:var(--radius);box-shadow:0 0 0 1px var(--hair);padding:16px;display:flex;flex-direction:column;gap:12px;min-width:0}
`;

const LD = {
  data: null,
  branches: [],
  merged: [],
  loaded: false,
  err: "",
  busy: {},
  diffKey: "",
  diffTitle: "",
  diffData: null,
  diffErr: "",
  diffLoading: false,
  diffEs: null,
  diffLive: false,
  collapsed: new Set(),
  diffMode: "inline",
  timer: 0,
  onPush: null,
};

let wrap = null;

async function loadLanding() {
  try {
    const [landRes, histRes] = await Promise.all([
      fetch("/api/landing", { cache: "no-store" }),
      fetch("/api/history?limit=40&view=next", { cache: "no-store" }),
    ]);
    if (!landRes.ok) throw new Error(`HTTP ${landRes.status}`);
    LD.data = await landRes.json();
    if (histRes.ok) {
      const h = await histRes.json();
      const entries = h.entries || [];
      LD.branches = entries.filter((w) => w.branch_name && !w.merged && !w.branch_discarded && !w.branch_gone);
      LD.merged = entries.filter((w) => w.merged).slice(0, 10);
    }
    LD.err = "";
  } catch (e) {
    LD.err = e.message || String(e);
  }
  LD.loaded = true;
}

function closeDiffStream() {
  if (LD.diffEs) { try { LD.diffEs.close(); } catch (_) {} LD.diffEs = null; }
  LD.diffLive = false;
}

async function openDiff(key, title, params) {
  closeDiffStream();
  if (LD.diffKey !== key) LD.collapsed.clear();
  LD.diffKey = key;
  LD.diffTitle = title;
  LD.diffLoading = true;
  LD.diffErr = "";
  LD.diffData = null;
  paint();
  const qs = new URLSearchParams(params).toString();
  if (typeof EventSource !== "undefined") {
    const es = new EventSource(`/api/landing/diff/stream?${qs}`);
    LD.diffEs = es;
    es.addEventListener("diff", (ev) => {
      if (LD.diffEs !== es) return;
      try {
        const d = JSON.parse(ev.data || "{}");
        LD.diffLoading = false;
        LD.diffLive = true;
        if (d.error) { LD.diffErr = d.error; LD.diffData = null; }
        else { LD.diffErr = ""; LD.diffData = d; }
      } catch (err) {
        LD.diffErr = err.message || String(err);
      }
      paint();
    });
    es.onerror = () => {
      if (LD.diffEs !== es) return;
      LD.diffLive = false;
      if (LD.diffLoading) { LD.diffErr = "Stream disconnected"; LD.diffLoading = false; }
      paint();
    };
    return;
  }
  try {
    const r = await fetch(`/api/landing/diff?${qs}`, { cache: "no-store" });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    LD.diffData = d;
  } catch (e) {
    LD.diffErr = e.message || String(e);
  }
  LD.diffLoading = false;
  paint();
}

async function runAction(url, body, okMsg) {
  const key = body.id || body.worker_id || url;
  LD.busy[key] = true;
  paint();
  try {
    const res = await api(url, body);
    if (res?.error) say(res.error, "error");
    else say(res?.result || okMsg, "success");
  } catch (e) {
    say(e.message || String(e), "error");
  }
  delete LD.busy[key];
  await loadLanding();
  paint();
}

function overlapRow(c) {
  const id = String(c.id || "");
  const busy = !!LD.busy[id];
  const files = (c.conflicts || []).join(", ") || "no files listed";
  const dels = (c.deletes_landed || []).length ? ` · deletions landed: ${c.deletes_landed.join(", ")}` : "";
  const st = String(c.status || "");
  const tone = st === "needs_resolution" ? "bad" : st === "resolved" ? "ok" : "";
  const diffKey = `conflict:${id}`;
  return `<div class="ld-row">
    <div class="ld-row-main">
      <div class="ld-title">${esc(c.branch || id)}</div>
      <div class="ld-meta">${esc(c.role || "worker")} · ${esc(files + dels)}</div>
    </div>
    <div class="ld-acts">
      <span class="ld-tag ${tone}">${esc(st.replace(/_/g, " "))}</span>
      ${c.resolver ? `<span class="ld-tag">${esc(c.resolver)}</span>` : ""}
      <button type="button" class="ld-btn" data-ld="diff-conflict" data-id="${esc(id)}" data-label="${esc(c.branch || id)}"${LD.diffKey === diffKey && LD.diffLoading ? " disabled" : ""}>View diff</button>
      ${st === "needs_resolution" ? `<button type="button" class="ld-btn primary" data-ld="resolve" data-id="${esc(id)}"${busy ? " disabled" : ""}>Resolve</button><button type="button" class="ld-btn danger" data-ld="drop" data-id="${esc(id)}"${busy ? " disabled" : ""}>Drop</button>` : ""}
    </div>
  </div>`;
}

function queueRow(e) {
  const key = `queue:${e.branch}:${e.worker_id || ""}`;
  return `<div class="ld-row">
    <div class="ld-row-main">
      <div class="ld-title">${esc(e.branch)}</div>
      <div class="ld-meta">${esc(e.role || "worker")} · ${esc(e.worker_id || "")}${e.repo ? ` · ${esc(e.repo)}` : ""}</div>
    </div>
    <div class="ld-acts">
      <span class="ld-tag">Queued</span>
      <button type="button" class="ld-btn" data-ld="diff-queue" data-key="${esc(key)}" data-branch="${esc(e.branch || "")}" data-base="${esc(e.base || "")}" data-repo="${esc(e.repo || "")}" data-wid="${esc(e.worker_id || "")}">View diff</button>
    </div>
  </div>`;
}

function branchRow(w) {
  const key = `worker:${w.id}`;
  return `<div class="ld-row">
    <div class="ld-row-main">
      <div class="ld-title">${esc(w.branch_name || w.role || w.id)}</div>
      <div class="ld-meta">${esc(w.role || "worker")} · ${esc(w.id)} · ${esc(String(w.status || "").replace(/_/g, " "))}</div>
    </div>
    <div class="ld-acts">
      <button type="button" class="ld-btn" data-ld="diff-worker" data-key="${esc(key)}" data-wid="${esc(w.id)}" data-label="${esc(w.branch_name || w.role || w.id)}">View diff</button>
      ${!w.merged ? `<button type="button" class="ld-btn primary" data-ld="merge-worker" data-wid="${esc(w.id)}" data-branch="${esc(w.branch_name)}"${LD.busy[w.id] ? " disabled" : ""}>Merge</button><button type="button" class="ld-btn danger" data-ld="discard-worker" data-wid="${esc(w.id)}" data-branch="${esc(w.branch_name)}"${LD.busy[w.id] ? " disabled" : ""}>Discard</button>` : ""}
    </div>
  </div>`;
}

function paint() {
  if (!wrap || !wrap.isConnected) return;
  const d = LD.data || {};
  const queue = d.queue || [];
  const conflicts = d.conflicts || [];
  const open = conflicts.filter((c) => c.status === "needs_resolution" || c.status === "resolving");
  const closed = conflicts.filter((c) => !open.includes(c)).slice(-10).reverse();

  const stats = `<div class="ld-stats">
    <div class="ld-stat"><b>${esc(d.land_mode || "merge")}</b><span>Land mode</span></div>
    <div class="ld-stat"><b>${queue.length}</b><span>Queued branches</span></div>
    <div class="ld-stat"><b>${open.length}</b><span>Open overlaps</span></div>
    <div class="ld-stat"><b>${LD.branches.length}</b><span>Unmerged worker branches</span></div>
  </div>`;

  const diffPanel = LD.diffKey ? `<section class="ld-diff-panel" aria-label="Patch diff">
    <div class="ld-head">
      <div><h2>Patch diff: ${esc(LD.diffTitle)}</h2><div class="ld-sub">${esc(LD.diffData?.base ? `${LD.diffData.base}..${LD.diffData.branch}` : LD.diffData?.branch || "")}</div></div>
      <div style="display:flex;gap:8px;align-items:center">${LD.diffLive ? `<span class="ld-tag ok">Live</span>` : ""}<button type="button" class="ld-btn" data-ld="close-diff">Close diff</button></div>
    </div>
    ${LD.diffLoading ? `<div class="ld-empty">Loading diff…</div>`
      : LD.diffErr ? `<div class="ld-empty">Couldn't load diff: ${esc(LD.diffErr)}</div>`
      : renderDiffHtml(LD.diffData?.diff || "", LD.diffMode, { truncated: !!LD.diffData?.truncated, collapsed: LD.collapsed })}
  </section>` : "";

  const overlapsCard = `<section class="ld-card">
    <div class="ld-head">
      <div><h2>Overlaps</h2><div class="ld-sub">Worker changes that overlapped HEAD. Resolve queues a resolver worker; Drop clears the overlap and keeps the branch.</div></div>
      <button type="button" class="ld-btn" data-ld="refresh">Refresh</button>
    </div>
    <div class="ld-rows">${!LD.loaded ? `<div class="ld-empty">Loading…</div>` : LD.err ? `<div class="ld-empty">Couldn't load landing: ${esc(LD.err)}</div>` : open.length ? open.map(overlapRow).join("") : `<div class="ld-empty">No overlaps waiting.</div>`}</div>
  </section>`;

  const queueCard = `<section class="ld-card">
    <div class="ld-head"><div><h2>Landing queue</h2><div class="ld-sub">Queued branches land when the repository is quiet.</div></div></div>
    <div class="ld-rows">${queue.length ? queue.map(queueRow).join("") : `<div class="ld-empty">Nothing queued.</div>`}</div>
  </section>`;

  const branchesCard = LD.branches.length ? `<section class="ld-card">
    <div class="ld-head"><div><h2>Unmerged worker branches</h2><div class="ld-sub">Recent worker branches preserved for review.</div></div></div>
    <div class="ld-rows">${LD.branches.slice(0, 15).map(branchRow).join("")}</div>
  </section>` : "";

  const mergedCard = LD.merged.length ? `<section class="ld-card">
    <div class="ld-head"><div><h2>Recently merged workers</h2><div class="ld-sub">Recent isolated workers already merged into the repository.</div></div></div>
    <div class="ld-rows">${LD.merged.map(branchRow).join("")}</div>
  </section>` : "";

  const closedCard = closed.length ? `<section class="ld-card">
    <div class="ld-head"><div><h2>Recently closed overlaps</h2></div></div>
    <div class="ld-rows">${closed.map(overlapRow).join("")}</div>
  </section>` : "";

  const dirtyCount = Number(d.working_changes || 0);
  const workingCard = `<section class="ld-card">
    <div class="ld-head"><div><h2>Working tree (physical changes)</h2><div class="ld-sub">${dirtyCount ? `${dirtyCount} uncommitted or untracked change${dirtyCount === 1 ? "" : "s"} on disk.` : "Working tree is clean."}</div></div>${dirtyCount ? `<button type="button" class="ld-btn" data-ld="diff-working">View diff</button>` : ""}</div>
  </section>`;

  wrap.innerHTML = stats + diffPanel + workingCard + overlapsCard + queueCard + branchesCard + mergedCard + closedCard;
}

function onClick(e) {
  const modeBtn = e.target.closest("[data-df-mode]");
  if (modeBtn) {
    LD.diffMode = modeBtn.dataset.dfMode === "split" ? "split" : "inline";
    paint();
    return;
  }
  const toggle = e.target.closest("[data-df-toggle]");
  if (toggle) {
    const name = toggle.dataset.dfToggle;
    if (LD.collapsed.has(name)) LD.collapsed.delete(name);
    else LD.collapsed.add(name);
    paint();
    return;
  }
  const b = e.target.closest("[data-ld]");
  if (!b || b.disabled) return;
  const act = b.dataset.ld;
  if (act === "refresh") { loadLanding().then(paint); return; }
  if (act === "close-diff") { closeDiffStream(); LD.collapsed.clear(); LD.diffKey = ""; LD.diffData = null; LD.diffErr = ""; if (location.hash.startsWith("#landing/")) location.hash = "#landing"; paint(); return; }
  if (act === "resolve") { runAction("/api/landing/resolve", { id: b.dataset.id }, "Resolver queued"); return; }
  if (act === "drop") { runAction("/api/landing/drop", { id: b.dataset.id }, "Dropped overlap"); return; }
  if (act === "merge-worker") { runAction("/api/landing/merge", { worker_id: b.dataset.wid }, "Branch merged"); return; }
  if (act === "discard-worker") { if (confirm(`Discard unmerged branch ${b.dataset.branch || b.dataset.wid}?`)) runAction("/api/landing/discard", { worker_id: b.dataset.wid }, "Branch discarded"); return; }
  if (act === "diff-working") { openDiff("working", "Working tree (uncommitted)", { working: "1" }); return; }
  if (act === "diff-conflict") { openDiff(`conflict:${b.dataset.id}`, b.dataset.label || b.dataset.id, { id: b.dataset.id }); return; }
  if (act === "diff-queue") {
    const p = { branch: b.dataset.branch };
    if (b.dataset.base) p.base = b.dataset.base;
    if (b.dataset.repo) p.repo = b.dataset.repo;
    if (b.dataset.wid) p.worker_id = b.dataset.wid;
    openDiff(b.dataset.key, b.dataset.branch, p);
    return;
  }
  if (act === "diff-worker") { openDiff(b.dataset.key, b.dataset.label || b.dataset.wid, { worker_id: b.dataset.wid }); }
}

function unmount() {
  clearTimeout(LD.timer);
  closeDiffStream();
  wrap?.remove();
  wrap = null;
}

function mount() {
  unmount();
  const [r, sub] = (location.hash.slice(1) || "home").split("/");
  if (r !== ROUTE) return;
  if (!document.getElementById("ld-css")) {
    document.head.append(Object.assign(document.createElement("style"), { id: "ld-css", textContent: CSS + DIFF_CSS }));
  }
  wrap = document.createElement("div");
  wrap.className = "ld-wrap";
  wrap.addEventListener("click", onClick);
  document.getElementById("page").append(wrap);
  if (sub) {
    const wid = decodeURIComponent(sub);
    if (wid === "working") openDiff("working", "Working tree (uncommitted)", { working: "1" });
    else openDiff(`worker:${wid}`, wid, { worker_id: wid });
  }
  paint();
  clearTimeout(LD.timer);
  loadLanding().then(paint);
  if (!LD.onPush) {
    LD.onPush = () => { if (wrap && wrap.isConnected) loadLanding().then(paint); };
    window.addEventListener("subway:push", LD.onPush);
  }
}

document.addEventListener("page:render", mount);
mount();
