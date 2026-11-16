/* Settings > Shared MCP: the sign-in list (port of hud/mcp_signin.js) and the servers Subway lends to workers.
   Fills the [data-mcp-slot] placeholders that settings.js renders, and refills them whenever settings re-renders.
   Never shows a server's env or headers: those can hold secrets. */
import { api } from "/hud/lib.js";

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const say = (m, k = "info") => { try { (window.toast?.[k] || window.toast || console.log)(m); } catch (_) {} };
let pushBound = false;
const S = { signin: null, signinErr: "", hub: null, hubErr: "", busy: {}, notes: {}, urls: {}, loading: false, poll: 0, skills: null, skillsErr: "", skQ: "", skAll: false, skOpen: {} };

async function load() {
  if (S.loading) return;
  S.loading = true;
  const [a, b] = await Promise.allSettled([api("/api/mcp/connect"), api("/api/mcp/hub/servers")]);
  if (a.status === "fulfilled") { S.signin = a.value; S.signinErr = ""; } else S.signinErr = String(a.reason?.message || a.reason);
  if (b.status === "fulfilled") { S.hub = b.value; S.hubErr = ""; } else S.hubErr = String(b.reason?.message || b.reason);
  S.loading = false;
  clearTimeout(S.poll);
  if ((S.signin?.servers || []).some((s) => s.pending === "waiting") && onPage()) S.poll = setTimeout(() => load().then(fill), 8000);
}

if (!pushBound) {
  pushBound = true;
  window.addEventListener("subway:push", () => {
    if (onPage() && (S.signin?.servers || []).some((s) => s.pending === "waiting")) load().then(fill);
  });
}

async function loadSkills() {
  try { S.skills = await api("/api/skills" + (S.skAll ? "?all=1" : "")); S.skillsErr = ""; }
  catch (e) { S.skillsErr = String(e.message || e); }
}

const LINE = { claude: "Claude Code", "claude-desktop": "Claude desktop", agy: "Antigravity", opencode: "OpenCode", codex: "Codex", cursor: "Cursor", copilot: "Copilot", pi: "pi", code: "VS Code", "kimi-desktop": "Kimi" };
function skillRows() {
  if (S.skillsErr) return msgRow("Couldn't load skills", S.skillsErr, `<button type="button" class="st-act" data-mcp-do="reload">Retry</button>`);
  if (!S.skills) return msgRow("Finding skills…");
  const q = S.skQ.trim().toLowerCase();
  const list = (S.skills.skills || []).filter((k) => !q || `${k.name} ${k.description}`.toLowerCase().includes(q));
  const groups = {};
  for (const k of list) (groups[k.line || "other"] ||= []).push(k);
  const order = Object.keys(groups).sort((a, b) => groups[b].length - groups[a].length || a.localeCompare(b));
  const head = `<div class="sk-bar"><input type="search" data-sk-q placeholder="Search ${(S.skills.skills || []).length} skills" value="${esc(S.skQ)}" aria-label="Search skills">
    <label class="sk-all"><input type="checkbox" data-sk-all${S.skAll ? " checked" : ""}> Include marketplace catalog</label></div>`;
  if (!order.length) return head + msgRow(q ? "No skills match" : "No skills found", q ? "Try another word." : "Install a skill in any agent and it appears here.");
  return head + order.map((ln) => {
    const items = groups[ln], open = !!S.skOpen[ln] || !!q, shown = open ? items : items.slice(0, 4);
    return `<div class="sk-grp"><div class="sk-gh"><b>${esc(LINE[ln] || ln)}</b><span class="mcp-tag">${items.length}</span></div>
      ${shown.map((k) => `<div class="st-row"><span class="st-txt"><span class="st-label">${esc(k.name)}${k.installed ? "" : ` <span class="mcp-tag">Catalog</span>`}${k.kind && k.kind !== "skill" ? ` <span class="mcp-tag">${esc(k.kind)}</span>` : ""}</span>${k.description ? `<span class="st-detail sk-d">${esc(k.description)}</span>` : ""}</span></div>`).join("")}
      ${items.length > shown.length ? `<button type="button" class="st-act sk-more" data-mcp-do="skopen" data-mcp-key="${esc(ln)}">Show all ${items.length}</button>` : ""}</div>`;
  }).join("");
}

async function act(key, action) {
  S.busy[key] = true; S.notes[key] = ""; fill();
  try {
    const res = await api("/api/mcp/connect", action === "github_app" ? { action, key } : { action, server: key });
    if (res?.error) S.notes[key] = res.error;
    else if (res?.authorize_url) { S.urls[key] = res.authorize_url; try { window.open(res.authorize_url, "_blank", "noopener"); } catch (_) {} }
    if (action !== "connect") delete S.urls[key];
    if (!res?.error) say({ connect: "Sign-in started", disconnect: "Disconnected", cancel: "Sign-in cancelled", github_app: "Finish on GitHub" }[action] || "Done", "success");
  } catch (e) { S.notes[key] = e.message || String(e); }
  S.busy[key] = false;
  await load(); fill();
}

const needsOwnApp = (s) => s.key === "github" && (!s.own_client || /register themselves/i.test(String(s.error || s.github_app_error || S.notes[s.key] || "")));
function detail(s) {
  if (S.notes[s.key]) return S.notes[s.key];
  if (s.github_app_pending) return "Finish on GitHub, then install the app";
  if (s.github_app_error) return `GitHub App failed: ${s.github_app_error}`;
  if (s.pending === "waiting") return s.user_code ? `Enter code ${s.user_code} on the page that opened` : "Sign in on the page that opened in your browser";
  if (s.pending === "error") return `Sign-in failed: ${s.pending_error || "unknown error"}`;
  if (s.key === "github" && s.github_app_slug && s.source !== "subway") return "GitHub App created. Install it, then connect.";
  if (s.source === "subway") {
    const mins = typeof s.expires_at === "number" ? Math.round((s.expires_at * 1000 - Date.now()) / 60000) : null;
    return "Subway token" + (mins == null ? "" : mins >= 0 ? ` · expires in ${mins} min` : " · refreshes on next use");
  }
  if (s.source === "borrowed") return "Borrowed from Claude Code";
  return s.status === "disconnected" ? "Signed out by the server. Connect again." : "Not signed in";
}
const btn = (label, key, action, busy) => `<button type="button" class="st-act" data-mcp-key="${esc(key)}" data-mcp-do="${action}"${busy ? ' disabled aria-busy="true"' : ""}>${esc(busy ? "Working…" : label)}</button>`;
const link = (label, href) => `<a class="st-act" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;
const msgRow = (label, d = "", trailing = "") => `<div class="st-row"><span class="st-txt"><span class="st-label">${esc(label)}</span>${d ? `<span class="st-detail">${esc(d)}</span>` : ""}</span>${trailing}</div>`;
const dot = (s) => `<span class="mcp-dot ${s.source === "subway" ? "on" : s.source === "borrowed" ? "lent" : s.pending === "waiting" ? "wait" : ""}" aria-hidden="true"></span>`;

function signinRows() {
  if (S.signinErr) return msgRow("Couldn't load sign-ins", S.signinErr, `<button type="button" class="st-act" data-mcp-do="reload">Retry</button>`);
  if (!S.signin) return msgRow("Loading sign-ins…");
  const list = S.signin.servers || [];
  if (!list.length) return msgRow("No HTTP MCP servers found", "Add one in Claude Code, Antigravity or OpenCode and it appears here.");
  return list.map((s) => {
    const action = s.pending === "waiting" ? "cancel" : s.source === "subway" ? "disconnect" : "connect";
    const text = { cancel: "Cancel", disconnect: "Disconnect", connect: "Connect" }[action];
    const busy = !!S.busy[s.key];
    let trailing;
    if (s.github_app_pending) trailing = `<span class="st-detail">Waiting for GitHub…</span>`;
    else if (needsOwnApp(s)) trailing = btn("Create my own GitHub App", s.key, "github_app", busy);
    else if (s.pending === "waiting" && S.urls[s.key]) trailing = `<span class="mcp-acts">${link("Open page", S.urls[s.key])}${btn(text, s.key, action, busy)}</span>`;
    else if (s.key === "github" && s.github_app_slug && s.source !== "subway") trailing = `<span class="mcp-acts">${link("Install app", `https://github.com/apps/${encodeURIComponent(s.github_app_slug)}/installations/new`)}${btn(text, s.key, action, busy)}</span>`;
    else trailing = btn(text, s.key, action, busy);
    return `<div class="st-row">${dot(s)}<span class="st-txt"><span class="st-label">${esc(s.name || s.key)}</span><span class="st-detail">${esc(detail(s) + (s.host ? ` · ${s.host}` : ""))}</span></span>${trailing}</div>`;
  }).join("");
}

const OWNER = { claude: "Claude Code", antigravity: "Antigravity", opencode: "OpenCode", codex: "Codex", gemini: "Gemini CLI", copilot: "Copilot" };
const ago = (ts) => { const s = Date.now() / 1000 - Number(ts || 0); if (!ts) return ""; return s < 60 ? "just now" : s < 3600 ? Math.round(s / 60) + "m ago" : s < 86400 ? Math.round(s / 3600) + "h ago" : Math.round(s / 86400) + "d ago"; };
function serverRows() {
  if (S.hubErr) return msgRow("Couldn't load servers", S.hubErr, `<button type="button" class="st-act" data-mcp-do="reload">Retry</button>`);
  if (!S.hub) return msgRow("Loading servers…");
  const run = new Map((S.hub.running || []).map((r) => [r.key, r]));
  const list = (S.hub.servers || []).slice().sort((a, b) => (run.get(b.key)?.alive ? 1 : 0) - (run.get(a.key)?.alive ? 1 : 0) || String(a.name).localeCompare(String(b.name)));
  if (!list.length) return msgRow("No MCP servers configured", "Servers set up in your agents are lent to workers automatically.");
  return list.map((s) => {
    const r = run.get(s.key) || {};
    const kind = s.transport === "http" ? "Remote" : "Local";
    const from = (s.owners || []).map((o) => OWNER[o] || o).join(", ");
    const state = r.alive ? `Running${r.tool_count ? ` · ${r.tool_count} tools` : ""}` : r.last_used ? `Idle · used ${ago(r.last_used)}` : "Idle";
    return `<div class="st-row"><span class="mcp-dot ${r.alive ? "on" : ""}" aria-hidden="true"></span><span class="st-txt"><span class="st-label">${esc(s.name || s.key)}</span><span class="st-detail">${esc(`${kind} · from ${from || "unknown"} · ${state}`)}</span></span>${s.supported === false ? `<span class="mcp-tag">Not supported</span>` : `<span class="mcp-tag">${esc(kind)}</span>`}</div>`;
  }).join("");
}

const CSS = `
[data-mcp-slot] .st-row+.st-row{box-shadow:inset 0 1px 0 var(--hair)}
[data-mcp-slot] .st-row{min-width:0}
[data-mcp-slot] .st-txt{min-width:0;flex:1}
[data-mcp-slot] .st-detail{overflow-wrap:anywhere}
.mcp-dot{width:8px;height:8px;border-radius:50%;background:#C9CFCB;flex-shrink:0}
.mcp-dot.on{background:#3E9C6B;box-shadow:0 0 0 3px rgba(62,156,107,.15)}
.mcp-dot.lent{background:var(--sky)}
.mcp-dot.wait{background:var(--ochre)}
.mcp-acts{display:inline-flex;gap:8px;flex-wrap:wrap}
a.st-act{text-decoration:none}
.sk-bar{display:flex;flex-direction:column;gap:8px;padding:10px 0}
.sk-bar input[type=search]{min-height:40px;width:100%;padding:0 12px;border-radius:10px;border:1px solid var(--hair);background:var(--ground);font:inherit}
.sk-all{display:flex;gap:8px;align-items:center;font-size:13px;color:var(--muted);cursor:pointer}
.sk-grp{padding:8px 0}
.sk-gh{display:flex;gap:8px;align-items:center;padding:6px 0;font-size:13px}
.sk-d{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.sk-more{margin:6px 0 0}
@media (min-width:768px){.sk-bar{flex-direction:row;align-items:center}.sk-bar input[type=search]{flex:1}}
.mcp-tag{font-size:11px;font-weight:600;padding:3px 8px;border-radius:99px;background:var(--ground);color:var(--muted);white-space:nowrap}
@media (max-width:639px){[data-mcp-slot] .st-row>.st-act,[data-mcp-slot] .st-row>.mcp-acts,[data-mcp-slot] .st-row>.mcp-tag{margin-left:auto}}
`;

const onPage = () => location.hash.slice(1).split("/")[0] === "settings" && location.hash.slice(1).split("/")[1] === "mcp";
function fill() {
  document.querySelectorAll('[data-mcp-slot="signin"]').forEach((n) => { n.innerHTML = signinRows(); });
  document.querySelectorAll('[data-mcp-slot="servers"]').forEach((n) => { n.innerHTML = serverRows(); });
  fillSkills();
}
function fillSkills() {
  document.querySelectorAll('[data-mcp-slot="skills"]').forEach((n) => {
    const f = document.activeElement && document.activeElement.matches("[data-sk-q]") ? document.activeElement.selectionStart : null;
    n.innerHTML = skillRows();
    if (f != null) { const i = n.querySelector("[data-sk-q]"); if (i) { i.focus(); i.setSelectionRange(f, f); } }
  });
}
function onClick(e) {
  const b = e.target.closest("[data-mcp-do]");
  if (!b || b.disabled) return;
  e.stopPropagation();
  if (b.dataset.mcpDo === "reload") { Promise.all([load(), loadSkills()]).then(fill); return; }
  if (b.dataset.mcpDo === "skopen") { S.skOpen[b.dataset.mcpKey] = true; fillSkills(); return; }
  act(b.dataset.mcpKey, b.dataset.mcpDo);
}

if (!document.getElementById("mcp-css")) document.head.append(Object.assign(document.createElement("style"), { id: "mcp-css", textContent: CSS }));
document.addEventListener("click", onClick, true);
let skT = 0;
document.addEventListener("input", (e) => {
  if (!e.target.matches?.("[data-sk-q]")) return;
  S.skQ = e.target.value; clearTimeout(skT); skT = setTimeout(fillSkills, 120);
});
document.addEventListener("change", (e) => {
  if (!e.target.matches?.("[data-sk-all]")) return;
  S.skAll = e.target.checked; S.skills = null; fillSkills(); loadSkills().then(fillSkills);
});
/* settings.js rebuilds the page on every change: refill empty slots as they appear. */
new MutationObserver(() => {
  const empty = [...document.querySelectorAll("[data-mcp-slot]")].some((n) => !n.dataset.filled || !n.firstElementChild);
  if (!empty) return;
  document.querySelectorAll("[data-mcp-slot]").forEach((n) => { n.dataset.filled = "1"; });
  fill();
}).observe(document.getElementById("page") || document.body, { childList: true, subtree: true });
function mount() { if (onPage()) { load().then(fill); if (!S.skills) loadSkills().then(fillSkills); } else clearTimeout(S.poll); }
document.addEventListener("page:render", mount);
window.addEventListener("hashchange", mount);
mount();
