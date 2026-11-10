// The Subway composer: a dispatch and steering bar, not a chat box.
//
// Blueprint: KokonutUI "ai-prompt" 2.0.0 (MIT, https://kokonutui.com), ported to the HUD's
// vanilla h() DOM: the frame with a one-line header, the box with a tray of chips, the
// auto-growing textarea, the "/" command menu, model + effort menus, and one Send/Stop button.
// Subway adds (design: Antigravity chat 24af35e8, stations 226-237):
//   - a target: new work, a running worker to steer (Stack or Recall now), or a chat to mail;
//   - "@" to pick that target, "&" lines to fan out parallel workers, #path:L-L code packets;
//   - a clock (run now, or after a worker finishes), a delegation mode (Auto, Isolate, Post
//     mail, Dry run) and the Principal (which chat receives the wakes).
// Every action goes through endpoints that exist today (/api/mcp/call, /api/claim,
// /api/preflight). Actions whose backend is still being built say so plainly when used.

import { api, get, h, modal, toast } from "./lib.js";
import { uploadAll, supported } from "./composer-attach.js";
import { fileSuggestions, weighPackets, exportThread } from "./composer-files.js";
import { li } from "./lineicons.js";
import { floatOpen } from "./float-pop.js";

const TOKEN = document.querySelector('meta[name="subway-token"]')?.content || "";
const WORKER_RE = /^worker_[0-9a-f]{6}$/;
const MAX_INLINE_FILE = 200 * 1024;
const TEXT_EXT = /\.(md|txt|py|js|mjs|ts|tsx|jsx|json|css|html|sh|toml|ya?ml|sql|rs|go|java|kt|swift|c|h|cpp|rb|php|diff|patch|log|csv|ini|cfg)$/i;
const EFFORT_ORDER = ["minimal", "low", "medium", "high"];
const EFFORT_HINTS = {
  Minimal: "Skips reasoning almost entirely",
  Low: "Answers right away",
  Medium: "Thinks briefly first",
  High: "Thinks longer on hard problems",
};
const PROVIDER_LABEL = { antigravity: "Antigravity", opencode: "OpenCode", local: "Local", copilot: "Copilot", claude: "Claude Code", pi: "Pi" };
const PROVIDER_ORDER = ["opencode", "antigravity", "local", "copilot", "claude", "pi"];

export const COMMANDS = [
  { id: "iterate", label: "Iterate", description: "Workers compete in rounds until the goal score", icon: "iterate" },
  { id: "broadcast", label: "Broadcast", description: "Post a decision to the track", icon: "broadcast" },
  { id: "mail", label: "Mail", description: "Send a note to a chat", icon: "mail" },
  { id: "claim", label: "Claim", description: "Lease a file before editing it", icon: "lock" },
  { id: "principal", label: "Principal", description: "Pass the baton to another chat", icon: "crown" },
  { id: "initiator", label: "Initiator", description: "Set which chat started this thread (type its id or alias)", icon: "crown" },
  { id: "rewind", label: "Rewind", description: "Fork the thread at a checkpoint", icon: "fork" },
  { id: "compact", label: "Compact", description: "Fold the thread into highlights", icon: "compact" },
  { id: "export", label: "Export", description: "Save this thread as md, json or a patch", icon: "stack" },
];

const MODES = [
  { id: "auto", label: "Auto", icon: "auto", hint: "One worker shares the tree; & lines get their own worktrees" },
  { id: "isolate", label: "Isolate", icon: "isolate", hint: "Every worker in its own worktree; changes return as patches" },
  { id: "mail", label: "Post mail", icon: "mail", hint: "Queue it, to run when the clock's condition is met" },
  { id: "dry", label: "Dry run", icon: "dry-run", hint: "Show the plan and the budget check; send nothing" },
];

/* ── Small pure helpers (exported for tests) ─────────────────────────── */

export function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const cap = (w) => (w ? w[0].toUpperCase() + w.slice(1) : w);

function prettyModel(id) {
  return id.replace(/:latest$/, "").split(/[-_]/).filter(Boolean)
    .map((w) => (/^v?\d/.test(w) ? w.toUpperCase().replace(/^V/, "V") : /^(gpt|glm|ai)$/i.test(w) ? w.toUpperCase() : cap(w)))
    .join(" ").replace(/\b(\d+)K\b/, "$1K");
}

/** Groups the flat /api/models catalogue into pickable models with effort levels. */
export function groupModels(catalog) {
  const groups = new Map();
  for (const m of catalog || []) {
    if (!m || !m.model) continue; // provider defaults ("") aren't a pickable model
    const suffix = m.provider === "antigravity" ? m.model.match(/-(minimal|low|medium|high)$/) : null;
    const base = suffix ? m.model.slice(0, -suffix[0].length) : m.model;
    const key = `${m.provider}|${base}`;
    let g = groups.get(key);
    if (!g) {
      const label = m.label ? m.label.replace(/\s*\((minimal|low|medium|high)\)\s*$/i, "") : "";
      const name = m.provider === "claude" ? `Claude ${cap(base)}` : (label || prettyModel(base));
      g = { key, provider: m.provider, base, name, ids: {}, efforts: [], exhausted: true, scarce: false };
      groups.set(key, g);
    }
    g.ids[suffix ? suffix[1] : "_"] = m.model;
    g.exhausted = g.exhausted && !!m.exhausted;
    g.scarce = g.scarce || !!m.scarce;
  }
  for (const g of groups.values()) {
    const levels = EFFORT_ORDER.filter((e) => g.ids[e]);
    if (levels.length) g.efforts = levels.map(cap);
    else if (g.provider === "claude" && /^(opus|sonnet)$/.test(g.base)) g.efforts = ["Low", "Medium", "High"];
  }
  return [...groups.values()].sort((a, b) =>
    (PROVIDER_ORDER.indexOf(a.provider) + 1 || 99) - (PROVIDER_ORDER.indexOf(b.provider) + 1 || 99) || a.name.localeCompare(b.name));
}

export function startEffort(g) {
  if (!g || !g.efforts.length) return null;
  return g.efforts.includes("Medium") ? "Medium" : g.efforts[g.efforts.length - 1];
}

/** The effort to keep when switching models: the same level if the new model has it, else the same height. */
export function carryEffort(from, to, effort) {
  const next = to ? to.efforts : [];
  if (!next.length) return null;
  if (effort && next.includes(effort)) return effort;
  const prev = from ? from.efforts : [];
  if (!effort || prev.length < 2) return startEffort(to);
  const share = prev.indexOf(effort) / (prev.length - 1);
  return next[Math.round(share * (next.length - 1))] || startEffort(to);
}

/** What a picked model + effort sends to spin_worker. Antigravity carries effort in the model id. */
export function resolveModel(g, effort) {
  if (!g) return { provider: "", model: "", effort: "auto" };
  const lvl = effort ? effort.toLowerCase() : null;
  if (lvl && g.ids[lvl]) return { provider: g.provider, model: g.ids[lvl], effort: "auto" };
  const model = g.ids._ || Object.values(g.ids)[0];
  return { provider: g.provider, model: g.provider === "claude" ? g.base : model, effort: lvl || "auto" };
}

export function modelMark(g) {
  const id = `${g ? g.base : ""}`.toLowerCase();
  const p = g ? g.provider : "";
  if (p === "claude" || /claude|opus|sonnet|haiku/.test(id)) return "claude";
  if (/gemini|argon/.test(id)) return "gemini";
  if (/deepseek/.test(id)) return "deepseek";
  if (/glm/.test(id)) return "glm";
  if (/kimi/.test(id)) return "kimi";
  if (/qwen/.test(id)) return "qwen";
  if (/mistral|devstral|codestral/.test(id)) return "mistral";
  if (/grok/.test(id)) return "xai";
  if (/^gpt|^o\d/.test(id)) return "openai";
  if (p === "local" || /gemma|llama/.test(id)) return "ollama";
  if (p === "copilot") return "copilot";
  return "code";
}

/**
 * Splits a message into worker briefs. Lines starting with "&" are parallel workers; any
 * other lines are shared context given to each. "#path:12-40" tokens become code packets.
 */
export function parseMessage(text) {
  const lines = String(text || "").split("\n");
  const fan = lines.filter((l) => /^\s*&/.test(l)).map((l) => l.replace(/^\s*&\s*/, "").trim()).filter(Boolean);
  const shared = lines.filter((l) => !/^\s*&/.test(l)).join("\n").trim();
  const briefs = fan.length ? fan.map((b) => (shared ? `${shared}\n\n${b}` : b)) : (shared ? [shared] : []);
  return briefs.map((brief) => {
    const code = [...brief.matchAll(/(?:^|\s)#([\w./-]+\.[\w]+:\d+-\d+)/g)].map((m) => m[1]);
    return { brief, code, role: roleOf(fan.length ? brief.split("\n").pop() : brief) };
  });
}

export function roleOf(text) {
  const words = String(text).replace(/#\S+/g, "").replace(/[^\w\s-]/g, " ").trim().split(/\s+/).filter(Boolean).slice(0, 5);
  const role = words.map(cap).join(" ");
  return (role || "Composer task").slice(0, 48);
}

/* ── Transport ───────────────────────────────────────────────────────── */

async function mcp(name, args) {
  const res = await fetch("/api/mcp/call", {
    method: "POST", cache: "no-store",
    headers: { "X-Subway-Token": TOKEN, "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) throw new Error((data.error && data.error.message) || `Request failed (${res.status})`);
  const text = ((data.result && data.result.content) || []).map((c) => c.text || "").join("\n").trim();
  if ((data.result && data.result.isError) || /^Error\b/.test(text)) throw new Error(text.replace(/^Error:?\s*/, ""));
  return text;
}

/** Endpoints the composer backend (docs/design/22-composer.md) adds. A 404 means "not built yet". */
async function composerApi(action, body) {
  const res = await fetch(`/api/composer/${action}`, {
    method: "POST", cache: "no-store",
    headers: { "X-Subway-Token": TOKEN, "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (res.status === 404) throw new Error(`${cap(action)} isn't wired yet: the composer backend is still being built.`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data.result || data.message || "Done.";
}

let catalogPromise = null;
function loadModels() {
  if (!catalogPromise) catalogPromise = api("/api/models").then((d) => groupModels(d.models)).catch(() => { catalogPromise = null; return []; });
  return catalogPromise;
}

const store = {
  read(key, fallback) { try { const v = localStorage.getItem(`subway.composer.${key}`); return v == null ? fallback : JSON.parse(v); } catch { return fallback; } },
  write(key, value) { try { localStorage.setItem(`subway.composer.${key}`, JSON.stringify(value)); } catch { /* private window */ } },
};

/* ── Popover menus ───────────────────────────────────────────────────── */

let openMenu = null;
function closeMenus() { if (openMenu) openMenu(); }

/** A trigger with a panel that opens upward, as the composer sits at the bottom. */
function dropdown(trigger, build, { align = "start", wide = false } = {}) {
  const panel = h("div", { class: `cmp-pop${wide ? " wide" : ""}`, role: "menu", hidden: true, "data-align": align });
  trigger.setAttribute("aria-haspopup", "menu");
  trigger.setAttribute("aria-expanded", "false");
  const items = () => [...panel.querySelectorAll("[data-item]:not([disabled])")];
  let unfloat = null;
  const onDoc = (e) => { if (!wrap.contains(e.target) && !panel.contains(e.target)) close(); };
  function close() {
    if (panel.hidden) return;
    panel.hidden = true;
    if (unfloat) { unfloat(); unfloat = null; }
    trigger.removeAttribute("data-state");
    trigger.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", onDoc, true);
    if (openMenu === close) openMenu = null;
  }
  function open() {
    closeMenus();
    panel.replaceChildren(...build(close).filter(Boolean));
    panel.hidden = false;
    unfloat = floatOpen(panel, trigger, { align });
    trigger.dataset.state = "open";
    trigger.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", onDoc, true);
    openMenu = close;
    const first = panel.querySelector("input") || panel.querySelector("[aria-checked='true']") || items()[0];
    if (first) first.focus({ preventScroll: true });
  }
  trigger.addEventListener("click", () => (panel.hidden ? open() : close()));
  panel.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); close(); trigger.focus(); return; }
    const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const list = items();
    const at = list.indexOf(document.activeElement);
    const next = list[(at + step + list.length) % list.length];
    if (next) next.focus();
  });
  const wrap = h("div", { class: "cmp-dd" }, trigger, panel);
  return { el: wrap, close, rebuild: () => { if (!panel.hidden) panel.replaceChildren(...build(close).filter(Boolean)); } };
}

function menuItem({ mark, iconName, title, hint, checked, disabled, onSelect, close, tag }) {
  return h("button", {
    class: "cmp-item", type: "button", role: checked == null ? "menuitem" : "menuitemradio", "data-item": "",
    "aria-checked": checked == null ? null : String(!!checked), disabled: !!disabled,
    on: { click: () => { if (close) close(); onSelect(); } },
  },
  h("span", { class: "cmp-item-ic" }, mark || (iconName ? li(iconName) : null)),
  h("span", { class: "cmp-item-txt" }, h("span", { class: "cmp-item-title" }, title, tag ? h("span", { class: "cmp-tag" }, tag) : null),
    hint ? h("span", { class: "cmp-item-hint" }, hint) : null),
  checked == null ? null : li("check", `cmp-item-check${checked ? "" : " off"}`));
}

const menuLabel = (text) => h("div", { class: "cmp-pop-label" }, text);

function effortBars(count, level) {
  return h("span", { class: "cmp-bars", "aria-hidden": "true" },
    Array.from({ length: count }, (_, i) => h("span", { class: i <= level ? "on" : "", style: `height:${((i + 1) / count) * 100}%` })));
}

function providerMark(g, cls = "") {
  return h("span", { class: `cmp-mark ${cls}`.trim(), style: g ? `--mark:var(--pv-${g.provider}, currentColor)` : null }, li(modelMark(g)));
}

/* ── The composer ────────────────────────────────────────────────────── */

function createComposer(context) {
  const st = {
    value: "", command: null, attachments: [], responding: false, menuIndex: 0, menuDismissed: false,
    target: null, steer: "stack", mode: store.read("mode", "auto"), clock: { kind: "now" },
    models: [], model: null, effort: null, lastSent: "", thread: null, chats: [],
    principal: store.read("principal", null), handoff: store.read("handoff", true),
    iter: store.read("iter", { goal: 8.5, maxRounds: 0, stall: 0, minutes: 10, tokens: 350000, overseer: "", rubric: "", participants: [] }),
    budget: "",
  };
  let nextId = 0;
  let abort = null;
  const uid = `cmp-${context}`;

  /* header */
  const headIcon = h("span", { class: "cmp-head-ic" });
  const headText = h("p", { class: "cmp-head-txt" });
  const steerSeg = h("div", { class: "cmp-seg", role: "radiogroup", "aria-label": "How to deliver", hidden: true });
  const clearTarget = h("button", { class: "cmp-head-btn icon", type: "button", "aria-label": "Clear target", hidden: true,
    on: { click: () => { setTarget(null); input.focus(); } } }, li("x"));
  const principalBtn = h("button", { class: "cmp-head-btn", type: "button", title: "The Principal receives worker wakes and permission requests" });
  const principalDd = dropdown(principalBtn, buildPrincipalMenu, { align: "end", wide: true });
  const head = h("div", { class: "cmp-head" }, headIcon, headText, steerSeg, clearTarget, principalDd.el);

  /* tray */
  const trayList = h("ul", { class: "cmp-tray-list", "aria-label": "Command and attachments" });
  const tray = h("div", { class: "cmp-tray", "data-open": "false" }, h("div", { class: "cmp-tray-in" }, trayList));

  /* slash / mention menu */
  const listbox = h("div", { class: "cmp-cmdmenu", id: `${uid}-menu`, role: "listbox", "aria-label": "Suggestions" });
  const cmdMenu = h("div", { class: "cmp-cmd", hidden: true }, listbox,
    h("div", { class: "cmp-cmd-foot" },
      h("span", {}, h("kbd", {}, "↑"), h("kbd", {}, "↓"), " to move"),
      h("span", {}, h("kbd", {}, "↵"), " to pick"),
      h("span", {}, h("kbd", {}, "esc"), " to close")));

  /* textarea */
  const input = h("textarea", {
    class: "cmp-input", id: `${uid}-input`, rows: "1", spellcheck: "true",
    "aria-autocomplete": "list", "aria-controls": `${uid}-menu`, "aria-expanded": "false",
  });
  const MIN_H = 56;
  const maxH = () => Math.max(240, Math.round(window.innerHeight * 0.5));
  function adjust(reset) {
    input.style.height = `${MIN_H}px`;
    if (reset && !input.placeholder) return;
    let need = input.scrollHeight;
    if (!input.value && input.placeholder) {
      input.value = input.placeholder;
      need = input.scrollHeight;
      input.value = "";
    }
    input.style.height = `${Math.max(MIN_H, Math.min(need, maxH()))}px`;
  }
  let lastW = 0;
  if (typeof ResizeObserver === "function") {
    new ResizeObserver(() => { const w = input.clientWidth; if (w && w !== lastW) { lastW = w; adjust(); } }).observe(input);
  }

  /* bar controls */
  const fileInput = h("input", { type: "file", multiple: true, class: "sr-only", tabindex: "-1", "aria-hidden": "true",
    on: { change: () => { if (fileInput.files.length) addFiles(fileInput.files); fileInput.value = ""; } } });
  const plusBtn = h("button", { class: "cmp-plus", type: "button", "aria-label": "Attach files and code packets", title: "Attach files, or drop or paste them" }, li("plus"));
  const plusDd = dropdown(plusBtn, buildPlusMenu);

  const modelName = h("span", { class: "cmp-model-name" });
  const modelMarkSlot = h("span", { class: "cmp-model-mark" });
  const modelBtn = h("button", { class: "cmp-ghost cmp-model", type: "button" }, modelMarkSlot, modelName, li("chevron-down", "cmp-chev"));
  const modelDd = dropdown(modelBtn, buildModelMenu, { wide: true });

  const effortSlot = h("span", { class: "cmp-effort-bars" });
  const effortLabel = h("span", { class: "cmp-lbl num" });
  const effortBtn = h("button", { class: "cmp-ghost", type: "button" }, effortSlot, effortLabel);
  const effortDd = dropdown(effortBtn, buildEffortMenu);

  const clockLabel = h("span", { class: "cmp-lbl" });
  const clockBtn = h("button", { class: "cmp-ghost", type: "button", title: "When it runs" }, li("clock"), clockLabel);
  const clockDd = dropdown(clockBtn, buildClockMenu);

  const modeIcon = h("span", { class: "cmp-mode-ic" });
  const modeLabel = h("span", { class: "cmp-lbl" });
  const modeBtn = h("button", { class: "cmp-ghost", type: "button", title: "Delegation mode" }, modeIcon, modeLabel);
  const modeDd = dropdown(modeBtn, buildModeMenu, { align: "end" });

  const sendBtn = h("button", { class: "cmp-send", type: "button" },
    h("span", { class: "cmp-face send", "aria-hidden": "true" }, li("arrow-up")),
    h("span", { class: "cmp-face stop", "aria-hidden": "true" }, h("span", { class: "cmp-stop-sq" })));

  const bar = h("div", { class: "cmp-bar" }, plusDd.el, fileInput, modelDd.el, effortDd.el, clockDd.el, modeDd.el,
    h("div", { class: "cmp-spacer" }), sendBtn);

  const drop = h("div", { class: "cmp-drop", "aria-hidden": "true" }, "Drop files to attach");
  st.queue = st.queue || [];
  const queueEl = h("div", { class: "cmp-queue", hidden: true });
  function renderQueue() {
    queueEl.hidden = !st.queue.length;
    queueEl.replaceChildren(...st.queue.map((q, i) => h("div", { class: "cmp-queue-item" },
      h("span", { class: "cmp-queue-txt" }, `Queued ${i + 1}: ${firstLine(q.value) || "attachment"}`),
      h("button", { class: "cmp-chip-x", type: "button", "aria-label": "Remove queued message",
        on: { click: () => { st.queue.splice(i, 1); renderQueue(); } } }, "×"))));
  }
  function queueCurrent() {
    if (!st.value.trim() && !st.attachments.length) return;
    st.queue.push({ value: st.value, command: st.command, attachments: st.attachments });
    st.value = ""; input.value = ""; st.attachments = []; st.command = null;
    adjust(true);
    renderQueue();
  }
  const resultSummary = h("summary", {}, "");
  const resultBody = h("pre", { class: "cmp-result-body" }, "");
  const resultEl = h("details", { class: "cmp-result", hidden: true }, resultSummary, resultBody);
  function showResult(text) {
    resultSummary.textContent = firstLine(text) || "Sent.";
    resultBody.textContent = String(text || "");
    resultEl.hidden = false;
  }
  const box = h("div", { class: "cmp-box" }, queueEl, tray, cmdMenu,
    h("label", { class: "sr-only", for: `${uid}-input` }, "Message"), input, bar, drop);

  /* footer: the plan in one line */
  const footPlan = h("span", { class: "cmp-foot-plan" });
  const footEdit = h("button", { class: "cmp-foot-btn", type: "button", hidden: true, on: { click: openIterationSettings } }, "Edit rounds");
  const footBudget = h("span", { class: "cmp-foot-budget" });
  const foot = h("div", { class: "cmp-foot" }, footPlan, footEdit, footBudget);

  const el = h("div", { class: "cmp", "data-context": context }, h("div", { class: "cmp-frame" }, head, box, resultEl, foot));

  /* ── state → view ── */

  function currentModel() { return st.models.find((g) => g.key === st.model) || null; }

  function slashQuery() {
    return !st.command && st.value.startsWith("/") && !/\s/.test(st.value) ? st.value.slice(1).toLowerCase() : null;
  }
  function mentionQuery() {
    const upto = st.value.slice(0, input.selectionStart ?? st.value.length);
    const m = upto.match(/(?:^|\s)@([\w.-]*)$/);
    return m ? m[1].toLowerCase() : null;
  }
  function hashQuery() {
    const upto = st.value.slice(0, input.selectionStart ?? st.value.length);
    const m = upto.match(/(?:^|\s)#([\w./-]*)$/);
    return m ? m[1] : null;
  }
  async function refreshFiles() {
    st.fileHits = st.fileHits || [];
    const hq = hashQuery();
    if (hq === null) {
      if (st.fileHits.length || st.fileQuery) { st.fileHits = []; st.fileQuery = null; renderMenu(); }
      return;
    }
    if (st.fileQuery === hq) return;
    st.fileQuery = hq;
    const hits = await fileSuggestions(hq);
    st.fileHits = hq === hashQuery() ? hits : [];
    renderMenu();
  }
  function suggestions() {
    const sq = slashQuery();
    if (sq !== null) return COMMANDS.filter((c) => c.label.toLowerCase().includes(sq)).map((c) => ({ kind: "command", c }));
    const hq = hashQuery();
    if (hq !== null) {
      return (st.fileHits || []).filter((f) => f.path.toLowerCase().includes(hq.toLowerCase())).slice(0, 8)
        .map((f) => ({ kind: "file", id: f.path, path: f.path, label: f.path, locked: f.locked, locked_by: f.locked_by,
          sub: `${f.lines} lines${f.locked ? ` · locked by ${f.locked_by || "someone"}` : ""}` }));
    }
    const mq = mentionQuery();
    if (mq === null) return [];
    if (!st.chats.length && !st.chatsLoading) {
      st.chatsLoading = true;
      api("/api/chats?limit=30&lite=1").then((d) => { st.chats = (d && d.chats) || []; renderMenu(); }).catch(() => {}).finally(() => { st.chatsLoading = false; });
    }
    const workers = (get("status")?.workers || []).filter((w) => ["running", "launching"].includes(w.status));
    const out = [];
    for (const w of workers) {
      const label = w.role || w.id;
      if (`${w.id} ${label}`.toLowerCase().includes(mq)) out.push({ kind: "worker", id: w.id, label, sub: [w.provider, w.model].filter(Boolean).join(" · ") });
    }
    for (const c of st.chats.slice(0, 30)) {
      const label = c.title || c.id;
      if (`${c.id} ${label} ${c.alias || ""}`.toLowerCase().includes(mq)) out.push({ kind: "chat", id: c.alias || c.id, label, sub: c.provider || c.source || "chat" });
    }
    return out.slice(0, 8);
  }

  let cmdFloat = null;
  function renderMenu() {
    refreshFiles();
    const list = suggestions();
    const open = list.length > 0 && !st.menuDismissed;
    cmdMenu.hidden = !open;
    if (open) {
      if (!cmdFloat) cmdFloat = floatOpen(cmdMenu, box, { matchWidth: true });
      requestAnimationFrame(() => { if (cmdFloat) cmdFloat.place(); });
    } else if (cmdFloat) { cmdFloat(); cmdFloat = null; }
    input.setAttribute("aria-expanded", String(open));
    if (!open) { input.removeAttribute("aria-activedescendant"); return; }
    const active = Math.min(st.menuIndex, list.length - 1);
    const sq = slashQuery();
    listbox.replaceChildren(...list.map((s, i) => {
      const id = `${uid}-opt-${i}`;
      const title = s.kind === "command" ? matched(s.c.label, sq || "") : s.kind === "file" ? [s.path] : [`@${s.label}`];
      return h("div", {
        class: "cmp-opt", id, role: "option", "aria-selected": String(i === active), tabindex: "-1",
        on: { mousedown: (e) => e.preventDefault(), pointermove: () => { if (st.menuIndex !== i) { st.menuIndex = i; renderMenu(); } }, click: () => pick(s) },
      },
      h("span", { class: "cmp-opt-ic" }, li(s.kind === "command" ? s.c.icon : s.kind === "worker" ? "steer" : s.kind === "file" ? (s.locked ? "lock" : "file") : "chat")),
      h("span", { class: "cmp-opt-title" }, ...title),
      h("span", { class: "cmp-opt-hint" }, s.kind === "command" ? s.c.description : s.sub));
    }));
    input.setAttribute("aria-activedescendant", `${uid}-opt-${active}`);
  }

  function matched(label, query) {
    const at = query ? label.toLowerCase().indexOf(query) : -1;
    if (at < 0) return [label];
    return [label.slice(0, at), h("b", {}, label.slice(at, at + query.length)), label.slice(at + query.length)];
  }

  function pick(s) {
    if (s.kind === "command") {
      st.command = s.c;
      st.value = "";
      input.value = "";
      if (s.c.id === "iterate") seedIteration();
    } else {
      const upto = st.value.slice(0, input.selectionStart ?? st.value.length);
      const rest = st.value.slice(upto.length);
      const cut = upto.replace(/(^|\s)@[\w.-]*$/, "$1");
      if (s.kind === "file") {
        st.value = cut + `#${s.path}:1-${s.lines}` + (rest && !/^\s/.test(rest) ? " " : "") + rest;
        input.value = st.value;
        st.fileHits = [];
        st.menuDismissed = true;
        st.menuIndex = 0;
        adjust();
        renderAll();
        input.focus();
        return;
      }
      st.value = cut + rest;
      input.value = st.value;
      setTarget({ kind: s.kind, id: s.id, label: s.label, sub: s.sub }, true);
    }
    st.menuIndex = 0;
    adjust();
    renderAll();
    input.focus();
  }

  function renderHead() {
    const t = st.target;
    headIcon.replaceChildren(li(t ? (t.kind === "worker" ? "steer" : "mail") : st.command ? st.command.icon : "fan-out"));
    if (t && t.kind === "worker") headText.textContent = `Steering ${t.label}${t.label !== t.id ? ` · ${t.id}` : ""}`;
    else if (t) headText.textContent = `Mail to ${t.label}`;
    else if (st.thread) headText.textContent = `New work · ${st.thread.title || "this thread"}`;
    else headText.textContent = "New work";
    steerSeg.hidden = !(t && t.kind === "worker");
    if (!steerSeg.hidden) {
      steerSeg.replaceChildren(...[["stack", "Stack", "Delivered at its next tool call"], ["recall", "Recall now", "Interrupts it now (⌘↵)"]].map(([id, label, title]) =>
        h("button", { type: "button", role: "radio", title, "aria-checked": String(st.steer === id), on: { click: () => { st.steer = id; renderAll(); } } }, label)));
    }
    clearTarget.hidden = !t;
    const p = st.principal;
    principalBtn.replaceChildren(li("crown"), h("span", { class: "cmp-lbl" }, p ? `@${p.label || p.id}` : "Principal"));
    principalBtn.setAttribute("aria-label", p ? `Principal: ${p.label || p.id}. Change` : "Principal: not set. Choose");
  }

  function renderTray() {
    const kids = [];
    if (st.command) {
      kids.push(h("li", { class: "cmp-chip cmd" }, h("span", { class: "cmp-slash", "aria-hidden": "true" }, "/"), st.command.label,
        h("button", { type: "button", class: "cmp-chip-x", "aria-label": `Remove ${st.command.label} command`,
          on: { click: () => { st.command = null; renderAll(); input.focus(); } } }, li("x"))));
    }
    for (const a of st.attachments) {
      const thumb = h("span", { class: "cmp-thumb" });
      if (a.preview) thumb.append(h("img", { alt: "", src: a.preview, width: "36", height: "36" }));
      else thumb.append(li(a.packet ? "code" : "file"));
      kids.push(h("li", { class: `cmp-chip file${a.unsupported ? " warn" : ""}` }, thumb,
        h("span", { class: "cmp-chip-txt" }, h("span", { class: "cmp-chip-name" }, a.name),
          h("span", { class: "cmp-chip-sub num" }, a.unsupported || (a.packet ? "code packet" : formatSize(a.size)))),
        h("button", { type: "button", class: "cmp-chip-x", "aria-label": `Remove ${a.name}`, on: { click: () => removeAttachment(a.id) } }, li("x"))));
    }
    trayList.replaceChildren(...kids);
    tray.dataset.open = String(kids.length > 0);
  }

  function renderBar() {
    const g = currentModel();
    modelMarkSlot.replaceChildren(g ? providerMark(g) : li("code"));
    modelName.textContent = g ? g.name : (st.models.length ? "Pick a model" : "Loading models…");
    modelBtn.setAttribute("aria-label", g ? `Model: ${g.name}. Change` : "Choose a model");
    const hideModel = !!st.target; // steering and mail reuse the target's own model
    modelDd.el.hidden = hideModel;
    const efforts = g ? g.efforts : [];
    effortDd.el.hidden = hideModel || !(efforts.length && st.effort);
    if (!effortDd.el.hidden) {
      effortSlot.replaceChildren(effortBars(efforts.length, Math.max(0, efforts.indexOf(st.effort))));
      effortLabel.textContent = st.effort;
      effortBtn.setAttribute("aria-label", `Effort: ${st.effort}. Change`);
    }
    const ck = st.clock;
    clockLabel.textContent = ck.kind === "now" ? "Now" : ck.kind === "after" ? `After ${ck.label || ck.id}` : ck.label;
    clockBtn.setAttribute("aria-label", `Runs: ${clockLabel.textContent}. Change`);
    clockDd.el.hidden = !!st.target;
    const mode = MODES.find((m) => m.id === st.mode) || MODES[0];
    modeIcon.replaceChildren(li(mode.icon));
    modeLabel.textContent = mode.label;
    modeBtn.setAttribute("aria-label", `Mode: ${mode.label}. Change`);
    modeDd.el.hidden = !!st.target;

    const can = canSend();
    sendBtn.dataset.state = st.responding ? "stop" : can ? "ready" : "idle";
    sendBtn.disabled = !(st.responding || can);
    sendBtn.setAttribute("aria-label", st.responding ? "Stop" : sendLabel());
    input.placeholder = st.responding ? "Sending… Enter queues the next message, Esc stops" : placeholder();
    if (!input.value) adjust();
  }

  function placeholder() {
    if (st.target && st.target.kind === "worker") return "Tell this worker what to change…";
    if (st.target) return "Write a note for this chat…";
    if (st.command) return { iterate: "The task the participants compete on…", broadcast: "The decision to post on track main…",
      mail: "The note (pick a chat with @)…", claim: "path/to/file.py and why…", principal: "Optional note for the new Principal…",
      rewind: "Which checkpoint, and why…", compact: "Optional focus for the summary…" }[st.command.id];
    return "Brief a worker. / for commands, @ to target, & for parallel lines";
  }
  function sendLabel() {
    if (st.target && st.target.kind === "worker") return st.steer === "recall" ? "Recall and redirect now" : "Stack instruction";
    if (st.target) return "Send note";
    if (st.mode === "dry") return "Preview the plan";
    return st.command ? `Run /${st.command.id}` : "Dispatch";
  }

  function renderFoot() {
    const g = currentModel();
    let plan = "";
    footEdit.hidden = true;
    if (st.target && st.target.kind === "worker") plan = st.steer === "recall" ? "Recalls the worker now and redirects it" : "Stacked: delivered at its next tool call";
    else if (st.target) plan = "A note in that chat's mailbox";
    else if (st.command && st.command.id === "iterate") {
      const n = st.iter.participants.length;
      plan = `${n} participant${n === 1 ? "" : "s"} · goal ${st.iter.goal}/10 · ${st.iter.minutes} min and ${Math.round(st.iter.tokens / 1000)}K tokens a round`;
      footEdit.hidden = false;
    } else if (st.command) plan = st.command.description;
    else {
      const n = parseMessage(st.value).length || 1;
      const iso = st.mode === "isolate" || (st.mode === "auto" && n > 1);
      plan = [`${n} worker${n === 1 ? "" : "s"}${g ? ` on ${g.name}${st.effort ? ` ${st.effort}` : ""}` : ""}`,
        iso ? "isolated worktrees" : "shared tree", st.mode === "dry" ? "dry run" : null,
        st.clock.kind === "after" ? `after ${st.clock.label || st.clock.id}` : null].filter(Boolean).join(" · ");
    }
    footPlan.textContent = plan;
    footBudget.textContent = st.target || st.command ? "" : st.budget;
  }

  function renderAll() {
    renderHead();
    renderTray();
    renderMenu();
    renderBar();
    renderFoot();
    principalDd.rebuild();
  }

  /* ── actions ── */

  function canSend() {
    if (st.responding) return false;
    if (st.command && ["principal", "initiator", "rewind", "compact"].includes(st.command.id)) return true;
    return st.value.trim().length > 0 || st.attachments.some((a) => !a.unsupported);
  }

  function setTarget(t, keepFocus) {
    st.target = t;
    renderAll();
    if (!keepFocus) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  async function addFiles(files) {
    for (const file of Array.from(files)) {
      nextId += 1;
      const a = { id: `att-${nextId}`, name: file.name || "pasted", size: file.size, file };
      if (file.type.startsWith("image/")) {
        a.preview = URL.createObjectURL(file);
        a.unsupported = "Not sent: workers can't take images yet";
      } else if (!(file.type.startsWith("text/") || TEXT_EXT.test(file.name)) ) {
        a.unsupported = "Not sent: only text files";
      } else if (file.size > MAX_INLINE_FILE) {
        a.unsupported = `Not sent: over ${formatSize(MAX_INLINE_FILE)}`;
      }
      st.attachments.push(a);
    }
    renderAll();
  }

  function removeAttachment(id) {
    const a = st.attachments.find((x) => x.id === id);
    if (a && a.preview) URL.revokeObjectURL(a.preview);
    st.attachments = st.attachments.filter((x) => x.id !== id);
    renderAll();
    input.focus();
  }

  /** Inline-safe files stay as text; the rest upload via composer-attach and ride along as paths. */
  async function prepareAttachments() {
    const inline = [], paths = [];
    for (const a of st.attachments) {
      if (a.unsupported || a.packet) continue;
      if (supported(a.file)) {
        const text = await a.file.text();
        inline.push(`--- attached: ${a.name} ---\n${text.replace(/\s+$/, "")}`);
        continue;
      }
      const up = await uploadAll([a.file]);
      if (up.failed.length) throw new Error(`Upload failed for ${a.name}: ${up.failed[0].error}`);
      paths.push(...up.paths);
    }
    return { extra: inline.length ? `\n\n${inline.join("\n\n")}` : "", paths };
  }

  function seedIteration() {
    if (st.iter.participants.length >= 2) return;
    const picks = [];
    const g = currentModel();
    if (g) picks.push(resolveModel(g, st.effort));
    for (const m of st.models) {
      if (picks.length >= 3) break;
      if (m.exhausted || picks.some((p) => p.model === resolveModel(m, startEffort(m)).model)) continue;
      if (["opencode", "antigravity"].includes(m.provider)) picks.push(resolveModel(m, startEffort(m)));
    }
    st.iter.participants = picks;
  }

  function spinStep(brief, code, role, g, attachments) {
    const r = resolveModel(g, st.effort);
    const n = parseMessage(st.value).length;
    const isolate = st.mode === "isolate" || (st.mode === "auto" && n > 1);
    const args = { role, prompt: brief, provider: r.provider, model: r.model, effort: r.effort, isolate, track: "main",
      route_reason: "Picked in the HUD composer" };
    if (code.length) args.code = code;
    if (attachments && attachments.length) args.attachments = attachments;
    if (st.thread && st.thread.id && context === "threads") args.route_reason += ` (thread ${st.thread.title || st.thread.id})`;
    return args;
  }

  async function dispatch() {
    const text = st.value;
    const att = await prepareAttachments();
    const extra = att.extra;
    const cmd = st.command ? st.command.id : null;
    const t = st.target;

    if (t && t.kind === "worker") {
      const now = st.steer === "recall";
      return mcp("instruct_worker", { worker_id: t.id, text: text + extra, attachments: att.paths, now });
    }
    if (t || cmd === "mail") {
      if (!t) throw new Error("Pick a chat with @ first.");
      return mcp("send_note", { to: t.id, text: text + extra });
    }
    if (cmd === "broadcast") return mcp("broadcast_track", { message: text, track: "main" });
    if (cmd === "claim") {
      const [filepath, ...why] = text.trim().split(/\s+/);
      const res = await api("/api/claim", { filepath, holder: "hud-user", duration: 300, purpose: why.join(" ") || "Claimed from the composer" });
      return res.result || res.message || `Claimed ${filepath}`;
    }
    if (cmd === "principal") {
      if (!(st.thread && st.thread.id)) return "No thread is open: the Principal needs one to hand off.";
      return composerApi("principal", { principal_id: st.principal && st.principal.id, note: text, auto_handoff: st.handoff, thread: st.thread && st.thread.id });
    }
    if (cmd === "initiator") {
      if (!(st.thread && st.thread.id)) return "No thread is open: pick one to set its initiator.";
      const [initiatorId, ...why] = text.trim().split(/\s+/);
      if (!initiatorId) return "Type the chat id or alias of the initiator.";
      return composerApi("initiator", { thread: st.thread.id, initiator_id: initiatorId, note: why.join(" ") });
    }
    if (cmd === "export") {
      if (!(st.thread && st.thread.id)) return "No thread is open to export.";
      const fmt = ["json", "patch"].includes(text.trim().toLowerCase()) ? text.trim().toLowerCase() : "md";
      const out = await exportThread(st.thread.id, fmt);
      const url = URL.createObjectURL(new Blob([out.body], { type: out.mime }));
      const link = document.createElement("a");
      link.href = url;
      link.download = out.filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      return `Exported ${st.thread.title || st.thread.id} as ${out.filename}.`;
    }
    if (cmd === "rewind") return composerApi("rewind", { thread: st.thread && st.thread.id, note: text });
    if (cmd === "compact") return composerApi("compact", { thread: st.thread && st.thread.id, note: text });
    if (cmd === "iterate") {
      const it = st.iter;
      if (it.participants.length < 2) throw new Error("Iteration needs at least 2 participants: use Edit rounds.");
      const args = { task: text + extra, participants: it.participants.map((p) => ({ provider: p.provider, model: p.model, effort: p.effort })),
        threshold: Number(it.goal), max_rounds: Number(it.maxRounds) || 0, stall_rounds: Number(it.stall) || 0,
        round_seconds: Math.round(Number(it.minutes) * 60), round_tokens: Number(it.tokens), track: "main" };
      if (it.rubric) args.rubric = it.rubric;
      if (it.overseer) { const [provider, model] = it.overseer.split("|"); args.overseer = { provider, model }; }
      if (st.mode === "dry") return `Dry run: would start an iteration with ${args.participants.length} participants, goal ${args.threshold}/10.`;
      return mcp("spin_iteration", args);
    }

    const g = currentModel();
    if (!g) throw new Error("Pick a model first.");
    const briefs = parseMessage(text);
    if (!briefs.length && extra) briefs.push({ brief: "Read the attached files and act on them.", code: [], role: "Composer Task" });
    let packetTokens = 0;
    const specs = briefs.flatMap((b) => b.code);
    if (specs.length) {
      const weighed = await weighPackets(specs);
      if (weighed.missing.length) return `Can't send: ${weighed.missing.length} packet spec${weighed.missing.length === 1 ? " is" : "s are"} out of range (${weighed.missing.join(", ")}).`;
      packetTokens = weighed.tokens;
    }
    const steps = briefs.map((b) => spinStep(b.brief + extra, b.code, b.role, g, att.paths));
    if (st.mode === "dry") {
      const pre = await api("/api/preflight", { provider: steps[0].provider, model: steps[0].model }).catch((e) => ({ message: e.message }));
      return `Dry run: ${steps.length} worker${steps.length === 1 ? "" : "s"} (${steps.map((s) => s.role).join(", ")}) on ${steps[0].model}, ${steps[0].isolate ? "isolated" : "shared tree"}.${packetTokens ? ` Packet weight: ${packetTokens} tokens.` : ""} ${pre.message || pre.result || ""}`.trim();
    }
    if (st.mode === "mail") return composerApi("mail", { steps, clock: st.clock, thread: st.thread && st.thread.id });
    if (st.clock.kind === "after") {
      const then = JSON.stringify(steps.map(({ route_reason, ...s }) => ({ ...s, when: "always" })));
      return mcp("on_worker_done", { worker_id: st.clock.id, then });
    }
    if (st.clock.kind !== "now") return composerApi("mail", { steps, clock: st.clock, thread: st.thread && st.thread.id });
    const out = [];
    for (const s of steps) out.push(await mcp("spin_worker", s));
    return out.join("\n");
  }

  async function submit() {
    if (!canSend()) return;
    const controller = new AbortController();
    abort = controller;
    const sent = { value: st.value, command: st.command, attachments: st.attachments };
    st.responding = true;
    renderAll();
    try {
      const result = await dispatch();
      if (controller.signal.aborted) return;
      st.lastSent = sent.value;
      for (const a of st.attachments) if (a.preview) URL.revokeObjectURL(a.preview);
      st.value = ""; input.value = ""; st.attachments = []; st.command = null;
      adjust(true);
      showResult(result);
      toast(firstLine(result) || "Sent.");
    } catch (err) {
      if (!controller.signal.aborted) toast(err.message || String(err));
    } finally {
      if (abort === controller) { abort = null; st.responding = false; renderAll(); }
      if (!controller.signal.aborted && st.queue.length && !st.value.trim()) {
        const next = st.queue.shift();
        st.value = next.value; input.value = next.value; st.command = next.command; st.attachments = next.attachments;
        renderQueue(); renderAll();
        setTimeout(submit, 0);
      }
    }
  }

  function stop() {
    if (abort) abort.abort();
    abort = null;
    st.responding = false;
    renderAll();
    input.focus();
  }

  const firstLine = (s) => String(s || "").split("\n").map((x) => x.replace(/^[#>*\s-]+/, "").trim()).find(Boolean) || "";

  /* ── menus ── */

  function buildPlusMenu(close) {
    const insert = (snippet) => {
      const v = st.value;
      st.value = v + (v && !v.endsWith("\n") ? "\n" : "") + snippet;
      input.value = st.value;
      adjust();
      renderAll();
      input.focus();
      input.setSelectionRange(st.value.length, st.value.length);
    };
    return [
      menuLabel("Attach"),
      menuItem({ iconName: "attach", title: "Files", hint: "Text files go in the brief; drop or paste works too", close, onSelect: () => fileInput.click() }),
      menuItem({ iconName: "code", title: "Code packet", hint: "#path:start-end, sent as exact lines", close, onSelect: () => insert("#path/to/file.py:1-40") }),
      menuLabel("Shape"),
      menuItem({ iconName: "fan-out", title: "Parallel line", hint: "& starts another worker with the same context", close, onSelect: () => insert("& ") }),
      menuItem({ iconName: "iterate", title: "Iteration", hint: "Workers compete in rounds", close, onSelect: () => pick({ kind: "command", c: COMMANDS[0] }) }),
      menuItem({ iconName: "fork", title: "Rewind or fork", hint: "From a checkpoint", close, onSelect: () => pick({ kind: "command", c: COMMANDS.find((c) => c.id === "rewind") }) }),
    ];
  }

  function buildModelMenu(close) {
    const filter = h("input", { class: "cmp-filter", type: "search", placeholder: "Filter models", "aria-label": "Filter models", autocomplete: "off" });
    const listEl = h("div", { class: "cmp-pop-scroll" });
    const fill = () => {
      const q = filter.value.trim().toLowerCase();
      const rows = [];
      let lastProvider = null;
      for (const g of st.models) {
        if (q && !`${g.name} ${g.base} ${g.provider}`.toLowerCase().includes(q)) continue;
        if (g.provider !== lastProvider) { rows.push(menuLabel(PROVIDER_LABEL[g.provider] || g.provider)); lastProvider = g.provider; }
        rows.push(menuItem({
          mark: providerMark(g), title: g.name, checked: g.key === st.model, close,
          hint: [g.base, g.efforts.length ? `${g.efforts.length} effort levels` : null].filter(Boolean).join(" · "),
          tag: g.exhausted ? "rate-limited" : g.scarce ? "scarce" : null,
          onSelect: () => { st.effort = carryEffort(currentModel(), g, st.effort); st.model = g.key; store.write("model", { key: g.key, effort: st.effort }); refreshBudget(); renderAll(); input.focus(); },
        }));
      }
      listEl.replaceChildren(...(rows.length ? rows : [h("div", { class: "cmp-pop-empty" }, st.models.length ? "No model matches." : "Loading models…")]));
    };
    filter.addEventListener("input", fill);
    filter.addEventListener("keydown", (e) => { if (e.key === "ArrowDown") { e.preventDefault(); listEl.querySelector("[data-item]")?.focus(); } });
    fill();
    return [filter, listEl];
  }

  function buildEffortMenu(close) {
    const g = currentModel();
    if (!g) return [];
    return [menuLabel("Effort"), ...[...g.efforts].reverse().map((e) => menuItem({
      mark: effortBars(g.efforts.length, g.efforts.indexOf(e)), title: e, hint: EFFORT_HINTS[e], checked: e === st.effort, close,
      onSelect: () => { st.effort = e; store.write("model", { key: st.model, effort: e }); refreshBudget(); renderAll(); input.focus(); },
    }))];
  }

  function buildClockMenu(close) {
    const workers = (get("status")?.workers || []).filter((w) => ["running", "launching"].includes(w.status)).slice(0, 8);
    const set = (clock) => { st.clock = clock; renderAll(); input.focus(); };
    return [
      menuLabel("Runs"),
      menuItem({ iconName: "auto", title: "Now", hint: "Queued for your approval right away", checked: st.clock.kind === "now", close, onSelect: () => set({ kind: "now" }) }),
      workers.length ? menuLabel("After a worker finishes") : null,
      ...workers.map((w) => menuItem({ iconName: "wait", title: w.role || w.id, hint: `${w.id} · ${[w.provider, w.model].filter(Boolean).join(" ")}`,
        checked: st.clock.kind === "after" && st.clock.id === w.id, close, onSelect: () => set({ kind: "after", id: w.id, label: w.role || w.id }) })),
      menuLabel("Post mail"),
      menuItem({ iconName: "clock", title: "When the quota resets", hint: "Needs the Post mail service", checked: st.clock.kind === "quota", close, onSelect: () => set({ kind: "quota", label: "Quota reset" }) }),
      menuItem({ iconName: "clock", title: "In one hour", hint: "Needs the Post mail service", checked: st.clock.kind === "in1h", close, onSelect: () => set({ kind: "in1h", label: "In 1 hour", at: Date.now() + 3600e3 }) }),
    ];
  }

  function buildModeMenu(close) {
    return [menuLabel("Delegation"), ...MODES.map((m) => menuItem({ iconName: m.icon, title: m.label, hint: m.hint, checked: st.mode === m.id, close,
      onSelect: () => { st.mode = m.id; store.write("mode", m.id); renderAll(); input.focus(); } }))];
  }

  function buildPrincipalMenu(close) {
    const chats = st.chats.slice(0, 10);
    const toggle = h("button", { class: "cmp-item", type: "button", role: "menuitemcheckbox", "data-item": "", "aria-checked": String(!!st.handoff),
      on: { click: () => { st.handoff = !st.handoff; store.write("handoff", st.handoff); toggle.setAttribute("aria-checked", String(st.handoff)); } } },
      h("span", { class: "cmp-item-ic" }, li("recall")),
      h("span", { class: "cmp-item-txt" }, h("span", { class: "cmp-item-title" }, "Auto-handoff"),
        h("span", { class: "cmp-item-hint" }, "Pass the baton when the Principal's quota is under 15% or it stalls")),
      h("span", { class: "cmp-switch", "aria-hidden": "true" }));
    return [
      menuLabel("Principal: receives wakes, permissions and leases"),
      ...chats.map((c) => menuItem({ iconName: "chat", title: c.title || c.id, hint: [c.alias, c.provider || c.source].filter(Boolean).join(" · "),
        checked: !!(st.principal && st.principal.id === (c.alias || c.id)), close,
        onSelect: () => {
          st.principal = { id: c.alias || c.id, label: c.alias || (c.title || c.id).slice(0, 24) };
          store.write("principal", st.principal);
          st.command = COMMANDS.find((x) => x.id === "principal");
          renderAll();
          input.focus();
        } })),
      chats.length ? null : h("div", { class: "cmp-pop-empty" }, "Open the Threads page to load chats."),
      toggle,
    ];
  }

  function openIterationSettings() {
    const it = st.iter;
    const opts = (sel) => st.models.filter((g) => !g.exhausted).flatMap((g) => (g.efforts.length ? g.efforts : [null]).map((e) => {
      const r = resolveModel(g, e);
      const key = `${r.provider}|${r.model}|${r.effort}`;
      return h("option", { value: key, selected: sel === key }, `${g.name}${e ? ` (${e})` : ""} · ${PROVIDER_LABEL[g.provider] || g.provider}`);
    }));
    const rowsEl = h("div", { class: "cmp-iter-rows" });
    const draft = it.participants.map((p) => `${p.provider}|${p.model}|${p.effort}`);
    const paint = () => rowsEl.replaceChildren(...draft.map((key, i) => h("div", { class: "cmp-iter-row" },
      h("select", { "aria-label": `Participant ${i + 1}`, on: { change: (e) => { draft[i] = e.target.value; } } }, opts(key)),
      h("button", { class: "btn", type: "button", disabled: draft.length <= 2, "aria-label": `Remove participant ${i + 1}`,
        on: { click: () => { draft.splice(i, 1); paint(); } } }, li("x")))),
    h("button", { class: "btn", type: "button", disabled: draft.length >= 4, on: { click: () => { draft.push(draft[draft.length - 1] || ""); paint(); } } }, li("plus"), " Add participant"));
    paint();
    const num = (label, key, attrs) => h("label", { class: "cmp-iter-field" }, h("span", {}, label), h("input", { type: "number", value: String(it[key]), ...attrs, "data-key": key }));
    const fields = h("div", { class: "cmp-iter-grid" },
      num("Goal score (1-10)", "goal", { min: "1", max: "10", step: "0.5" }),
      num("Max rounds (0 = no limit)", "maxRounds", { min: "0", step: "1" }),
      num("Stop after N flat rounds (0 = never)", "stall", { min: "0", step: "1" }),
      num("Minutes per round", "minutes", { min: "1", step: "1" }),
      num("Tokens per round", "tokens", { min: "10000", step: "10000" }),
      h("label", { class: "cmp-iter-field" }, h("span", {}, "Overseer (optional)"),
        h("select", { "data-key": "overseer" }, h("option", { value: "" }, "None"),
          ...st.models.filter((g) => !g.exhausted).map((g) => { const r = resolveModel(g, g.efforts[g.efforts.length - 1] || null);
            const v = `${r.provider}|${r.model}`; return h("option", { value: v, selected: it.overseer === v }, `${g.name} · ${PROVIDER_LABEL[g.provider] || g.provider}`); }))));
    const rubric = h("textarea", { class: "cmp-iter-rubric", rows: "3", placeholder: "Rubric (optional): what a 10/10 means" });
    rubric.value = it.rubric || "";
    const save = h("button", { class: "btn primary", type: "button" }, "Save");
    const body = h("div", { class: "cmp-iter" }, h("div", { class: "row-sub" }, "Participants (2 to 4)"), rowsEl, fields, rubric, h("div", { class: "btns" }, save));
    const dlg = modal("Iteration rounds", body);
    save.addEventListener("click", () => {
      it.participants = draft.filter(Boolean).map((k) => { const [provider, model, effort] = k.split("|"); return { provider, model, effort }; });
      for (const f of fields.querySelectorAll("[data-key]")) it[f.dataset.key] = f.tagName === "SELECT" ? f.value : Number(f.value);
      it.rubric = rubric.value.trim();
      store.write("iter", it);
      dlg.close();
      renderAll();
    });
  }

  let budgetTimer = 0;
  function refreshBudget() {
    clearTimeout(budgetTimer);
    budgetTimer = setTimeout(async () => {
      const r = resolveModel(currentModel(), st.effort);
      if (!r.model) return;
      try {
        const res = await api("/api/preflight", { provider: r.provider, model: r.model });
        st.budget = res.ok === false ? `Budget: ${firstLine(res.message)}` : "Budget ok";
      } catch (e) { st.budget = `Budget: ${e.message}`; }
      renderFoot();
    }, 250);
  }

  /* ── events ── */

  input.addEventListener("input", () => {
    st.value = input.value;
    st.menuDismissed = false;
    st.menuIndex = 0;
    adjust();
    renderAll();
  });
  input.addEventListener("click", () => renderMenu());
  input.addEventListener("focus", () => { st.menuDismissed = false; renderMenu(); });
  input.addEventListener("blur", () => { st.menuDismissed = true; renderMenu(); });
  input.addEventListener("paste", (e) => {
    if (e.clipboardData && e.clipboardData.files.length) { e.preventDefault(); addFiles(e.clipboardData.files); }
  });
  input.addEventListener("keydown", (e) => {
    if (e.isComposing) return; // Enter confirms an IME candidate; it must not send
    const list = cmdMenu.hidden ? [] : suggestions();
    if (list.length) {
      const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
      const active = Math.min(st.menuIndex, list.length - 1);
      if (step) { e.preventDefault(); st.menuIndex = (active + step + list.length) % list.length; renderMenu(); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(list[active]); return; }
      if (e.key === "Escape") { e.preventDefault(); st.menuDismissed = true; renderMenu(); return; }
    }
    if (st.value === "") {
      if (e.key === "ArrowUp" && st.lastSent) {
        e.preventDefault();
        st.value = st.lastSent; input.value = st.value; adjust(); renderAll();
        input.setSelectionRange(st.value.length, st.value.length);
        return;
      }
      if (e.key === "Backspace" && st.command) { e.preventDefault(); st.command = null; renderAll(); return; }
      if (e.key === "Backspace" && st.target) { e.preventDefault(); setTarget(null, true); return; }
    }
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && st.target && st.target.kind === "worker") {
      e.preventDefault(); st.steer = "recall"; submit(); return;
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (st.responding) queueCurrent(); else submit(); return; }
    if (e.key === "Escape" && st.responding) { e.preventDefault(); stop(); }
  });
  sendBtn.addEventListener("click", () => (st.responding ? stop() : submit()));

  const hasFiles = (e) => Array.from((e.dataTransfer && e.dataTransfer.types) || []).includes("Files");
  box.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); box.dataset.drag = "1"; } });
  box.addEventListener("dragleave", (e) => { if (!box.contains(e.relatedTarget)) delete box.dataset.drag; });
  box.addEventListener("drop", (e) => { if (!hasFiles(e)) return; e.preventDefault(); delete box.dataset.drag; addFiles(e.dataTransfer.files); });

  loadModels().then((models) => {
    st.models = models;
    const saved = store.read("model", null);
    const g = models.find((m) => saved && m.key === saved.key)
      || models.find((m) => !m.exhausted && /-free$/.test(`${m.base || ""}`))
      || models.find((m) => !m.exhausted) || models[0];
    if (g) { st.model = g.key; st.effort = saved && saved.key === g.key && g.efforts.includes(saved.effort) ? saved.effort : startEffort(g); }
    refreshBudget();
    renderAll();
  });

  adjust(true);
  renderAll();

  return {
    el,
    update(ctx = {}) {
      if ("thread" in ctx) st.thread = ctx.thread || null;
      if (Array.isArray(ctx.chats)) st.chats = ctx.chats;
      if (st.target && st.target.kind === "worker") {
        const w = (get("status")?.workers || []).find((x) => x.id === st.target.id);
        if (w && !["running", "launching"].includes(w.status)) { st.target = null; toast(`${w.role || w.id} has finished, so steering was cleared.`); }
      }
      renderAll();
    },
    target(t) { setTarget(t); input.focus({ preventScroll: true }); },
  };
}

/* ── Public API ──────────────────────────────────────────────────────── */

const instances = new Map();

/**
 * The composer for one place in the HUD. The page re-renders on every poll, so one instance per
 * context is kept and re-mounted: the draft, the model and the target survive re-renders.
 */
export function composerBar(context = "threads", ctx = {}) {
  let inst = instances.get(context);
  if (!inst) { inst = createComposer(context); instances.set(context, inst); }
  inst.update(ctx);
  return inst.el;
}

/** Points a composer at a worker to steer or a chat to mail: { kind: "worker"|"chat", id, label, sub }. */
export function steerFromComposer(target, context = "threads") {
  const inst = instances.get(context);
  if (inst) inst.target(target);
}

export const isWorkerId = (id) => WORKER_RE.test(String(id || ""));

/** Kept for existing callers: aiPrompt({ context, thread, chats }). */
export function aiPrompt(opts = {}) {
  return composerBar(opts.context || "threads", { thread: opts.thread, chats: opts.chats });
}
export default aiPrompt;

document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenus(); });
