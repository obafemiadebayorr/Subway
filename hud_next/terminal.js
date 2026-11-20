/* Terminal page (prototype): terminals Subway knows about, what each is sharing, and their command history.
   Real data only: /api/terminals, /api/terminal?id=, /api/pending_runs; writes go to /api/terminal_sharing,
   /api/terminal_opt_in and /api/terminal_log. Mirrors the old HUD's rules: a greyed switch can still be turned
   off, only not on; allowing commands and capturing output each need an inline "Allow?" first. */
import { api } from "/hud/lib.js";

const ROUTE = "terminal";
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const say = (msg, kind = "info") => { try { (window.toast?.[kind] || window.toast || console.log)(msg); } catch (_) {} };
const ago = (ts) => {
  ts = Number(ts || 0);
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
};
const failed = (code) => code !== 0 && code !== null && code !== undefined;

/* ---------- state ---------- */
const S = { loaded: false, error: "", rows: [], sharing: false, runs: [], detail: {}, busy: false };
const ui = { open: "", confirm: "", capture: "", del: "", outputs: new Set(), view: "open" };

async function load() {
  const [t, r] = await Promise.allSettled([api("/api/terminals"), api("/api/pending_runs")]);
  if (t.status === "fulfilled") {
    S.rows = t.value.terminals || [];
    S.sharing = !!t.value.sharing;
    S.error = "";
  } else S.error = String(t.reason?.message || t.reason);
  S.runs = r.status === "fulfilled" ? r.value.runs || [] : [];
  S.loaded = true;
  if (ui.open) await loadDetail(ui.open);
}

async function loadDetail(id) {
  try { S.detail[id] = (await api("/api/terminal?id=" + encodeURIComponent(id))).terminal || {}; }
  catch (e) { S.detail[id] = { error: e.message || String(e) }; }
}

async function post(path, body, done) {
  if (S.busy) return;
  S.busy = true;
  try { await api(path, body); if (done) say(done, "success"); }
  catch (e) { say(e.message || String(e), "error"); }
  S.busy = false;
  await load();
  render();
}

/* ---------- pieces ---------- */
const kindOf = (t) => (t.source === "hook" ? (t.app === "terminal" ? "Terminal.app" : t.app === "iterm" ? "iTerm2" : "Shell hook") : "Editor");
const nativeHook = (t) => t.source === "hook" && (t.app === "terminal" || t.app === "iterm");
const otherHook = (t) => t.source === "hook" && !nativeHook(t);

function sw(label, on, act, id, disabled, hint = "") {
  const hid = `tm-h-${act}-${esc(id)}`;
  return `<div class="tm-sw-row${disabled ? " is-off" : ""}">
    <button type="button" class="tm-sw" role="switch" aria-checked="${on ? "true" : "false"}" aria-label="${esc(label)}"${hint ? ` aria-describedby="${hid}"` : ""} data-act="${act}" data-id="${esc(id)}"${disabled ? " disabled" : ""}><i></i></button>
    <span><span class="tm-sw-l" aria-hidden="true">${esc(label)}</span>${hint ? `<small id="${hid}">${esc(hint)}</small>` : ""}</span></div>`;
}

function confirmBox(kind, t) {
  const text = kind === "allow"
    ? `Agents will be able to run commands in ${t.name || t.id}. Allow?`
    : "Subway will read this terminal's text after each command (secrets are redacted). Allow?";
  return `<div class="tm-confirm" role="group" aria-label="${kind === "allow" ? "Allow commands" : "Capture output"} in ${esc(t.name || t.id)}">
    <p>${esc(text)}</p>
    <div class="tm-btns"><button type="button" class="tm-btn primary" data-act="${kind}-yes" data-id="${esc(t.id)}">Allow</button>
    <button type="button" class="tm-btn" data-act="cancel" data-id="${esc(t.id)}">Cancel</button></div></div>`;
}

function tags(t) {
  const out = [`<span class="tm-tag">${esc(kindOf(t))}</span>`];
  if (t.shell_integration === false) out.push(`<span class="tm-tag">no shell integration</span>`);
  if (t.failures) out.push(`<span class="tm-tag bad">${t.failures} failing</span>`);
  if (t.command_count) out.push(`<span class="tm-tag">${t.command_count} command${t.command_count === 1 ? "" : "s"}</span>`);
  return out.join("");
}

function historyPane(t) {
  const d = S.detail[t.id];
  if (!d) return `<div class="tm-hist"><p class="tm-muted">Loading history…</p></div>`;
  if (d.error) return `<div class="tm-hist"><p class="tm-err">Couldn't load this terminal: ${esc(d.error)}</p></div>`;
  const cmds = (d.commands || []).map((c, i) => ({ c, key: t.id + ":" + (c.ts || "i" + i) })).reverse().slice(0, 60);
  if (!cmds.length) return `<div class="tm-hist"><p class="tm-muted">No commands recorded yet.${t.share_enabled ? "" : " Share this terminal to start recording."}</p></div>`;
  return `<ol class="tm-hist" aria-label="Command history, newest first">${cmds.map(({ c, key }) => {
    const shown = ui.outputs.has(key);
    const bad = failed(c.exit_code);
    return `<li class="${bad ? "bad" : ""}">
      <div class="tm-cmd"><code>$ ${esc(c.command)}</code>
        <span class="tm-exit${bad ? " bad" : ""}" title="Exit code">${esc(c.exit_code ?? "?")}</span></div>
      <div class="tm-meta"><span>${c.ts ? esc(new Date(c.ts * 1000).toLocaleString()) : ""}${c.caller_id && c.caller_id !== "editor" ? ` · by ${esc(c.caller_id)}` : ""}</span>
        <span class="tm-meta-act">
          <button type="button" class="tm-link" data-act="output" data-key="${esc(key)}" aria-expanded="${shown}">${shown ? "Hide output" : "Show output"}</button>
          ${bad ? `<button type="button" class="tm-link" data-act="handoff" data-id="${esc(t.id)}" data-key="${esc(key)}">Copy for a worker</button>` : ""}
        </span></div>
      ${shown ? `<pre class="tm-out">${esc(c.output || "(no output)")}</pre>` : ""}
    </li>`;
  }).join("")}</ol>`;
}

function terminalCard(t) {
  const shared = !!t.share_enabled;
  const allowDisabled = (!S.sharing || !shared || otherHook(t)) && !t.allow_commands;
  const captureDisabled = (!S.sharing || !shared) && !t.capture_output;
  const open = ui.open === t.id;
  const stamp = ago(t.last_ts);
  return `<article class="tm-card${open ? " is-open" : ""}" aria-label="${esc(t.name || t.id)}">
    <button type="button" class="tm-main" data-act="toggle" data-id="${esc(t.id)}" aria-expanded="${open}">
      <span class="tm-dot${shared && S.sharing ? " on" : ""}" aria-hidden="true"></span>
      <span class="tm-title"><b>${esc(t.name || t.id)}</b>
        <small>${esc(t.folder || "Unknown folder")}${stamp ? " · " + esc(stamp) : ""}</small></span>
      ${t.last_command ? `<code class="tm-last${failed(t.last_exit_code) ? " bad" : ""}">$ ${esc(t.last_command)}</code>` : `<span class="tm-muted tm-last">No commands yet</span>`}
      <svg class="tm-chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>
    <div class="tm-ctrl">
      <div class="tm-sws">
        ${sw("Shared with agents", shared, "share", t.id, !S.sharing && !shared, S.sharing ? "" : "Turn on terminal sharing first")}
        ${sw("Agents may run commands", !!t.allow_commands, "allow", t.id, allowDisabled, otherHook(t) ? "Needs an editor terminal with the Subway extension" : "")}
        ${nativeHook(t) ? sw("Capture output", !!t.capture_output, "capture", t.id, captureDisabled, `macOS asks once to let Subway control ${t.app === "terminal" ? "Terminal.app" : "iTerm2"}`) : ""}
      </div>
      <div class="tm-tags">${tags(t)}</div>
      ${ui.confirm === t.id && !allowDisabled ? confirmBox("allow", t) : ""}
      ${ui.capture === t.id && !captureDisabled ? confirmBox("capture", t) : ""}
    </div>
    ${open ? historyPane(t) : ""}
  </article>`;
}

function closedRow(t) {
  const asking = ui.del === t.id;
  return `<li class="tm-closed-row">
    <span class="tm-title"><b>${esc(t.name || t.id)}</b><small>${esc(t.folder || "")}${t.closed_at ? " · closed " + esc(ago(t.closed_at)) : ""} · ${t.command_count || 0} commands</small></span>
    <span class="tm-btns">${asking
      ? `<span class="tm-ask">Delete its log for good?</span><button type="button" class="tm-btn danger" data-act="del-yes" data-id="${esc(t.id)}">Delete</button><button type="button" class="tm-btn" data-act="del-no">Keep</button>`
      : `<button type="button" class="tm-btn" data-act="archive" data-id="${esc(t.id)}">Archive</button><button type="button" class="tm-btn ghost" data-act="del" data-id="${esc(t.id)}">Delete</button>`}</span>
  </li>`;
}

function runsPane() {
  if (!S.runs.length) return "";
  return `<section class="tm-sec" aria-labelledby="tm-runs-h"><div class="tm-h"><h2 id="tm-runs-h">Waiting to run</h2><p>Commands agents asked to run in a shared terminal</p></div>
    <ul class="tm-runs">${S.runs.map((r) => {
      const term = S.rows.find((t) => t.id === r.terminal_id);
      return `<li><code>$ ${esc(r.command || "")}</code><span class="tm-meta">${esc(term ? term.name || term.id : r.terminal_id || "unknown terminal")}${r.created_at ? " · " + esc(ago(r.created_at)) : ""}</span><span class="tm-tag ${r.status === "running" ? "sky" : "gold"}">${esc(r.status || "pending")}</span></li>`;
    }).join("")}</ul></section>`;
}

function render() {
  if (!wrap) return;
  if (!S.loaded) { wrap.innerHTML = `<p class="tm-muted">Loading terminals…</p>`; return; }
  const open = S.rows.filter((t) => !t.closed);
  const closed = S.rows.filter((t) => t.closed);
  const sharedN = open.filter((t) => t.share_enabled).length;
  const allowN = open.filter((t) => t.allow_commands).length;
  const failN = open.reduce((a, t) => a + (t.failures || 0), 0);
  const master = `<section class="tm-master${S.sharing ? " on" : ""}" aria-labelledby="tm-master-h">
      <div><h2 id="tm-master-h">Terminal sharing ${S.sharing ? "is on" : "is off"}</h2>
        <p>${S.sharing ? "Agents can read the terminals you share below, and run commands only where you allow it." : "Agents can't see or use any terminal. Nothing is recorded."}</p></div>
      <button type="button" class="tm-sw big" role="switch" aria-checked="${S.sharing}" aria-label="Terminal sharing" data-act="master"><i></i></button>
    </section>`;
  const stats = `<dl class="tm-stats">
      <div><dt>Open</dt><dd>${open.length}</dd></div>
      <div><dt>Shared</dt><dd>${sharedN}</dd></div>
      <div><dt>Agents can run</dt><dd>${allowN}</dd></div>
      <div class="${failN ? "bad" : ""}"><dt>Failing commands</dt><dd>${failN}</dd></div>
    </dl>`;
  const list = open.length
    ? `<div class="tm-list">${open.map(terminalCard).join("")}</div>`
    : `<div class="tm-empty"><b>No open terminals</b><p>Open a terminal in your editor with the Subway extension, or add the Subway shell hook to Terminal.app or iTerm2. It appears here, and nothing is shared until you switch it on.</p></div>`;
  const closedSec = closed.length ? `<details class="tm-sec tm-closed"${ui.view === "closed" ? " open" : ""}>
      <summary><span><b>Closed terminals</b> <span class="tm-muted">${closed.length}, logs kept</span></span>
        <span class="tm-btns">${ui.del === "*all*"
          ? `<span class="tm-ask">Delete every closed log?</span><button type="button" class="tm-btn danger" data-act="delall-yes">Delete all</button><button type="button" class="tm-btn" data-act="del-no">Keep</button>`
          : `<button type="button" class="tm-btn" data-act="archiveall">Archive all</button><button type="button" class="tm-btn ghost" data-act="delall">Delete all</button>`}</span></summary>
      <ul class="tm-closed-list">${closed.map(closedRow).join("")}</ul></details>` : "";
  wrap.innerHTML = `${S.error ? `<p class="tm-err" role="alert">Couldn't load terminals: ${esc(S.error)}</p>` : ""}${master}${stats}
    <section class="tm-sec" aria-labelledby="tm-open-h"><div class="tm-h"><h2 id="tm-open-h">Open terminals</h2><p>Select one to see its history</p></div>${list}</section>
    ${runsPane()}${closedSec}`;
}

/* ---------- events ---------- */
function onClick(e) {
  const b = e.target.closest("button");
  if (!b || !wrap.contains(b) || b.disabled) return;
  if (b.closest("summary")) e.preventDefault();
  const act = b.dataset.act, id = b.dataset.id || "";
  const t = S.rows.find((r) => r.id === id) || {};
  switch (act) {
    case "master":
      if (S.sharing) { ui.confirm = ui.capture = ""; }
      return post("/api/terminal_sharing", { enabled: !S.sharing }, S.sharing ? "Terminal sharing off" : "Terminal sharing on");
    case "toggle":
      ui.open = ui.open === id ? "" : id;
      render();
      if (ui.open) loadDetail(id).then(render);
      return;
    case "share":
      if (t.share_enabled) { if (ui.confirm === id) ui.confirm = ""; if (ui.capture === id) ui.capture = ""; }
      return post("/api/terminal_opt_in", { terminal_id: id, share_enabled: !t.share_enabled }, t.share_enabled ? "Stopped sharing" : "Shared with agents");
    case "allow":
      if (t.allow_commands) return post("/api/terminal_opt_in", { terminal_id: id, allow_commands: false }, "Agents can no longer run commands here");
      ui.confirm = id; render(); return;
    case "capture":
      if (t.capture_output) return post("/api/terminal_opt_in", { terminal_id: id, capture_output: false }, "Output capture off");
      ui.capture = id; render(); return;
    case "allow-yes": ui.confirm = ""; return post("/api/terminal_opt_in", { terminal_id: id, allow_commands: true }, "Agents may run commands here");
    case "capture-yes": ui.capture = ""; return post("/api/terminal_opt_in", { terminal_id: id, capture_output: true }, "Output capture on");
    case "cancel": ui.confirm = ui.capture = ""; render(); return;
    case "output": {
      const k = b.dataset.key;
      if (ui.outputs.has(k)) ui.outputs.delete(k); else ui.outputs.add(k);
      render(); return;
    }
    case "handoff": {
      const rec = S.detail[id] || {};
      const c = (rec.commands || []).find((x, i) => id + ":" + (x.ts || "i" + i) === b.dataset.key) || {};
      const text = `Terminal \`${rec.name || id}\` ran \`${c.command || ""}\` (exit ${c.exit_code}) in ${rec.folder || "an unknown folder"}:\n\n${String(c.output || "").slice(0, 4000)}`;
      Promise.resolve(navigator.clipboard?.writeText(text)).then(() => say("Copied: paste it into a worker brief", "success"), () => say("Couldn't copy", "error"));
      return;
    }
    case "archive": ui.view = "closed"; return post("/api/terminal_log", { terminal_id: id, action: "archive" }, "Log archived");
    case "del": ui.view = "closed"; ui.del = id; render(); return;
    case "del-yes": ui.del = ""; return post("/api/terminal_log", { terminal_id: id, action: "delete" }, "Log deleted");
    case "del-no": ui.del = ""; render(); return;
    case "archiveall": ui.view = "closed"; return post("/api/terminal_log", { all_closed: true, action: "archive" }, "Closed logs archived");
    case "delall": ui.view = "closed"; ui.del = "*all*"; render(); return;
    case "delall-yes": ui.del = ""; return post("/api/terminal_log", { all_closed: true, action: "delete" }, "Closed logs deleted");
  }
}

/* ---------- styles (mobile first; tablet and desktop layer on) ---------- */
const CSS = `
.tm{display:flex;flex-direction:column;gap:16px;margin-top:16px;min-width:0;color:var(--ink)}
.tm button{font:inherit}
.tm code,.tm pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.tm-muted{color:var(--muted)}
.tm-err{color:#A23B2A;font-size:13px;margin:0}
.tm-h{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:4px 8px;margin:0 0 10px}
.tm-h h2{margin:0;font-size:15px;font-weight:600}
.tm-h p{margin:0;font-size:12px;color:var(--muted)}
.tm-btns{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.tm-btn{display:inline-flex;align-items:center;min-height:34px;padding:0 12px;border-radius:9px;border:1px solid var(--hair);background:var(--surface);color:var(--ink);font-size:12.5px;font-weight:500;cursor:pointer;white-space:nowrap}
.tm-btn.primary{background:var(--ink);border-color:var(--ink);color:#fff}
.tm-btn.ghost{border-color:transparent;background:transparent;color:var(--muted)}
.tm-btn.danger{color:#fff;background:#A23B2A;border-color:#A23B2A}
.tm-link{all:unset;cursor:pointer;color:var(--sky-ink,var(--ink));font-size:12px;text-decoration:underline;text-underline-offset:2px}
.tm button:focus-visible,.tm summary:focus-visible{outline:2px solid var(--ochre);outline-offset:2px}
.tm-tag{display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;background:var(--ground);color:var(--muted);white-space:nowrap}
.tm-tag.bad{background:rgba(162,59,42,.1);color:#A23B2A}
.tm-tag.gold{background:rgba(212,166,74,.16);color:#8a6418}
.tm-tag.sky{background:var(--sky-chip);color:var(--sky-ink)}
.tm-tags{display:flex;flex-wrap:wrap;gap:4px}

.tm-sw{all:unset;cursor:pointer;flex-shrink:0;width:38px;height:22px;border-radius:999px;background:var(--hair);position:relative;transition:background .15s}
.tm-sw i{position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;background:var(--surface);box-shadow:0 1px 2px color-mix(in srgb,var(--ink) 20%,transparent);transition:transform .15s}
.tm-sw[aria-checked="true"]{background:var(--sky-deep,var(--sky-ink))}
.tm-sw[aria-checked="true"] i{transform:translateX(16px)}
.tm-sw:disabled{cursor:not-allowed;opacity:.45}
.tm-sw.big{width:46px;height:26px}
.tm-sw.big i{width:20px;height:20px}
.tm-sw.big[aria-checked="true"] i{transform:translateX(20px)}
.tm-sw-row{display:flex;gap:10px;align-items:flex-start;font-size:13px;min-width:0}
.tm-sw-row > span{display:flex;flex-direction:column;gap:2px;min-width:0;padding-top:2px}
.tm-sw-row small{font-size:11.5px;color:var(--muted)}
.tm-sw-row.is-off .tm-sw-l{color:var(--muted)}

.tm-master{display:flex;gap:14px;align-items:center;justify-content:space-between;padding:16px;border-radius:var(--radius);background:var(--surface);box-shadow:0 0 0 1px var(--hair),0 1px 2px color-mix(in srgb,var(--ink) 3%,transparent)}
.tm-master h2{margin:0 0 4px;font-size:15px;font-weight:600}
.tm-master p{margin:0;font-size:12.5px;color:var(--muted);max-width:60ch}

.tm-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:0}
.tm-stats div{padding:10px 12px;border-radius:var(--radius);background:var(--surface);box-shadow:0 0 0 1px var(--hair),0 1px 2px color-mix(in srgb,var(--ink) 3%,transparent);min-width:0}
.tm-stats dt{font-size:11.5px;color:var(--muted)}
.tm-stats dd{margin:2px 0 0;font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}
.tm-stats .bad dd{color:#A23B2A}

.tm-list{display:grid;gap:10px}
.tm-card{background:var(--surface);border-radius:var(--radius);box-shadow:0 0 0 1px var(--hair),0 1px 2px color-mix(in srgb,var(--ink) 3%,transparent);min-width:0;overflow:hidden}
.tm-main{all:unset;box-sizing:border-box;cursor:pointer;display:grid;grid-template-columns:auto minmax(0,1fr) auto;grid-template-areas:"dot title chev" ". last last";gap:4px 10px;align-items:center;width:100%;padding:14px 14px 10px}
.tm-dot{grid-area:dot;width:9px;height:9px;border-radius:50%;background:var(--hair)}
.tm-dot.on{background:#3E9C6B;box-shadow:0 0 0 3px rgba(62,156,107,.15)}
.tm-title{grid-area:title;display:flex;flex-direction:column;min-width:0}
.tm-title b{font-weight:600;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tm-title small{font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tm-last{grid-area:last;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:12px}
.tm-last.bad{color:#A23B2A}
.tm-chev{grid-area:chev;width:16px;height:16px;color:var(--muted);transition:transform .15s}
.tm-card.is-open .tm-chev{transform:rotate(180deg)}
.tm-ctrl{display:flex;flex-direction:column;gap:10px;padding:0 14px 14px}
.tm-sws{display:grid;gap:10px}
.tm-confirm{background:var(--ground);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:8px}
.tm-confirm p{margin:0;font-size:12.5px}

.tm-hist{list-style:none;margin:0;padding:10px 14px 14px;border-top:1px solid var(--hair);display:grid;gap:8px;max-height:440px;overflow:auto}
.tm-hist li{padding:8px 10px;border-radius:10px;background:var(--ground);min-width:0}
.tm-cmd{display:flex;gap:8px;align-items:flex-start;justify-content:space-between;min-width:0}
.tm-cmd code{white-space:pre-wrap;word-break:break-word;min-width:0}
.tm-exit{flex-shrink:0;font:600 11px ui-monospace,Menlo,monospace;padding:1px 6px;border-radius:6px;background:rgba(62,156,107,.14);color:#2c7a52}
.tm-exit.bad{background:rgba(162,59,42,.12);color:#A23B2A}
.tm-meta{display:flex;flex-wrap:wrap;gap:4px 10px;justify-content:space-between;font-size:11.5px;color:var(--muted);margin-top:4px}
.tm-meta-act{display:inline-flex;gap:12px}
.tm-out{margin:8px 0 0;padding:10px;border-radius:8px;background:#14212b;color:#dfe7ea;white-space:pre-wrap;word-break:break-word;max-height:260px;overflow:auto}

.tm-runs{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.tm-runs li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 10px;align-items:center;padding:10px 12px;border-radius:var(--radius);background:var(--surface);box-shadow:0 0 0 1px var(--hair),0 1px 2px color-mix(in srgb,var(--ink) 3%,transparent)}
.tm-runs code{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tm-runs .tm-meta{grid-column:1;margin:0}
.tm-runs .tm-tag{grid-row:1 / span 2;grid-column:2}

.tm-closed{background:var(--surface);border-radius:var(--radius);box-shadow:0 0 0 1px var(--hair),0 1px 2px color-mix(in srgb,var(--ink) 3%,transparent);padding:0}
.tm-closed summary{cursor:pointer;list-style:none;display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;padding:12px 14px;font-size:13.5px}
.tm-closed summary::-webkit-details-marker{display:none}
.tm-closed-list{list-style:none;margin:0;padding:0 14px 12px;display:grid;gap:6px}
.tm-closed-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;padding:8px 0;border-top:1px solid var(--hair)}
.tm-ask{font-size:12.5px;color:#A23B2A}
.tm-empty{padding:18px;background:var(--surface);border-radius:var(--radius);box-shadow:0 0 0 1px var(--hair),0 1px 2px color-mix(in srgb,var(--ink) 3%,transparent);font-size:13px}
.tm-empty p{margin:6px 0 0;color:var(--muted);max-width:62ch}

@media (min-width:640px){
  .tm-stats{grid-template-columns:repeat(4,minmax(0,1fr))}
  .tm-sws{grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
}
@media (min-width:1024px){
  .tm-main{grid-template-columns:auto minmax(0,1.1fr) minmax(0,1fr) auto;grid-template-areas:"dot title last chev"}
  .tm-ctrl{flex-direction:row;flex-wrap:wrap;align-items:flex-start;justify-content:space-between}
  .tm-sws{flex:1 1 520px}
  .tm-confirm{flex-basis:100%}
}
@media (prefers-reduced-motion:reduce){.tm-sw,.tm-sw i,.tm-chev{transition:none}}
`;

/* ---------- mount ---------- */
let wrap = null, timer = 0, onPush = null;
function unmount() {
  clearInterval(timer); timer = 0;
  if (onPush) { window.removeEventListener("subway:push", onPush); onPush = null; }
  wrap?.remove(); wrap = null;
}
function mount() {
  unmount();
  const r = (location.hash.slice(1) || "home").split("/")[0];
  if (r !== ROUTE && r !== "terminals") return;
  if (!document.getElementById("tm-css")) document.head.append(Object.assign(document.createElement("style"), { id: "tm-css", textContent: CSS }));
  wrap = document.createElement("div");
  wrap.className = "tm";
  wrap.addEventListener("click", onClick);
  document.getElementById("page").append(wrap);
  render();
  load().then(render);
  onPush = () => { if (wrap?.isConnected && !document.hidden && !ui.confirm && !ui.capture && !ui.del && !S.busy) load().then(render); };
  window.addEventListener("subway:push", onPush);
}
document.addEventListener("page:render", mount);
mount();
