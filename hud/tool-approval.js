/**
 * @author: @kokonutui
 * @description: AI Tool Approval
 * @version: 1.0.0
 * @date: 2026-10-15
 * @license: MIT
 * @website: https://kokonutui.com
 * @github: https://github.com/kokonut-labs/kokonutui
 *
 * Ported to vanilla JS for Subway HUD: no React, no motion/react, no build
 * step. Icons are vendored from lucide-static (ISC) with their exact paths.
 */
import { h, s, act } from "./lib.js";

const EASE_OUT = "cubic-bezier(0.23, 1, 0.32, 1)";
const HOLD_MS = 900;
const TRACE_MS = 60_000;

const TERMINAL_TOOL = /bash|shell|terminal|exec|command/;
const FILE_TOOL = /write|edit|file|patch/;
const WEB_TOOL = /fetch|web|browse|http|search/;
const SEND_TOOL = /mail|send|message|post/;

/* ---- Module-level UI state, so HUD poll re-renders never lose it ---- */

// id -> { call, at, enter } — decided calls, shown as trace lines for 60s.
const answered = new Map();
// id -> { always, open, holding, holdStarted, timer } — per-card UI state.
const uiState = new Map();
// ids whose current card has already had its enter animation.
const seenCards = new Set();
let refocusNext = false;
let lastRoot = null;

const uiFor = (id) => {
  let st = uiState.get(id);
  if (!st) uiState.set(id, (st = { always: false, open: false, holding: false, holdStarted: 0, timer: null }));
  return st;
};

/* ---- lucide-static v0.460.0 icons (ISC), exact vendored paths ---- */

const LUCIDE = {
  "chevron-right": [h => s("path", { d: "m9 18 6-6-6-6" })],
  "file-pen": [
    h => s("path", { d: "M12.5 22H18a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2v9.5" }),
    h => s("path", { d: "M14 2v4a2 2 0 0 0 2 2h4" }),
    h => s("path", { d: "M13.378 15.626a1 1 0 1 0-3.004-3.004l-5.01 5.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z" }),
  ],
  globe: [
    h => s("circle", { cx: "12", cy: "12", r: "10" }),
    h => s("path", { d: "M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" }),
    h => s("path", { d: "M2 12h20" }),
  ],
  send: [
    h => s("path", { d: "M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z" }),
    h => s("path", { d: "m21.854 2.147-10.94 10.939" }),
  ],
  "square-terminal": [
    h => s("path", { d: "m7 11 2-2-2-2" }),
    h => s("path", { d: "M11 13h4" }),
    h => s("rect", { width: "18", height: "18", x: "3", y: "3", rx: "2", ry: "2" }),
  ],
  wrench: [
    h => s("path", { d: "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" }),
  ],
};

function lucideIcon(name) {
  return s("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: "ta-tool-svg" }, LUCIDE[name].map((p) => p()));
}

function ToolIcon(call) {
  const name = String(call.tool || "").toLowerCase();
  if (TERMINAL_TOOL.test(name)) return lucideIcon("square-terminal");
  if (FILE_TOOL.test(name)) return lucideIcon("file-pen");
  if (WEB_TOOL.test(name)) return lucideIcon("globe");
  if (SEND_TOOL.test(name)) return lucideIcon("send");
  return lucideIcon("wrench");
}

/* ---- Input: a command or text shows as code; an object shows as fields ---- */

const inputSummary = (input) => (typeof input === "string" ? input : Object.values(input)[0]?.toString());

function ToolInput(input) {
  if (typeof input === "string") {
    return h("pre", { class: "ta-input" },
      h("span", { class: "ta-prompt", "aria-hidden": "true" }, "$ "),
      input);
  }
  return h("dl", { class: "ta-input-grid" },
    Object.entries(input).map(([key, value]) => [
      h("dt", { class: "ta-input-key" }, key),
      h("dd", { class: "ta-input-val" }, String(value)),
    ]));
}

/* ---- HoldToAllow: the red fill wipes across while held and runs back
   quickly when let go early, so a slip never confirms ---- */

function HoldToAllow(id, onConfirm) {
  const st = uiFor(id);
  let timer = st.timer;
  const btn = h("button", {
    class: "ta-hold" + (st.holding ? " ta-holding" : ""),
    "aria-label": "Allow. Press and hold to confirm",
    type: "button",
    on: {
      blur: () => stop(),
      contextmenu: (event) => event.preventDefault(),
      keydown: (event) => {
        if ((event.key === " " || event.key === "Enter") && !event.repeat) {
          event.preventDefault();
          start();
        }
      },
      keyup: (event) => {
        if (event.key === " " || event.key === "Enter") stop();
      },
      pointercancel: () => stop(),
      pointerdown: (event) => {
        // A right click or a pen's barrel button is not a hold.
        if (event.button === 0) start();
      },
      pointerleave: () => stop(),
      pointerup: () => stop(),
    },
  }, "Hold to allow",
  h("span", { class: "ta-hold-fill", "aria-hidden": "true" }, "Hold to allow"));

  const start = () => {
    // A key and a pointer can both start a hold; keep only one timer.
    if (timer) clearTimeout(timer);
    st.holding = true;
    st.holdStarted = Date.now();
    btn.classList.add("ta-holding");
    timer = setTimeout(() => {
      timer = st.timer = null;
      st.holding = false;
      st.holdStarted = 0;
      onConfirm();
    }, HOLD_MS);
    st.timer = timer;
  };
  const stop = () => {
    st.holding = false;
    st.holdStarted = 0;
    if (timer) clearTimeout(timer);
    timer = st.timer = null;
    btn.classList.remove("ta-holding");
  };

  // Survive a poll re-render mid-hold: restart with the remaining time.
  if (st.holding) {
    const remaining = HOLD_MS - (Date.now() - st.holdStarted);
    timer = setTimeout(() => {
      timer = st.timer = null;
      st.holding = false;
      st.holdStarted = 0;
      onConfirm();
    }, Math.max(remaining, 0));
    st.timer = timer;
  }
  return btn;
}

/* ---- ApprovalCard ---- */

function ApprovalCard(call, onRespond) {
  const st = uiFor(call.id);
  const alwaysId = `ta-always-${call.id}`;
  const titleId = `ta-title-${call.id}`;

  const card = h("section", {
    "aria-labelledby": titleId,
    class: "ta-card",
    "data-call-id": call.id,
    tabindex: "-1",
    on: {
      keydown: (event) => {
        if (event.key === "Escape") onRespond(false, false);
      },
    },
  },
    h("header", { class: "ta-card-head" },
      h("span", { class: "ta-icon-badge" }, ToolIcon(call)),
      h("h3", { class: "ta-title", id: titleId }, call.title),
      h("span", { class: "ta-tool mono" }, call.tool),
      call.destructive ? h("span", { class: "ta-badge" }, "Destructive") : null),
    ToolInput(call.input),
    call.reason ? h("p", { class: "ta-reason" }, call.reason) : null,
    h("div", { class: "ta-actions" },
      call.destructive
        ? h("span", { class: "ta-asksevery" }, "Asks every time")
        : h("label", { class: "ta-always", for: alwaysId, title: (call.rules || []).join(", ") },
            h("input", {
              checked: st.always,
              class: "ta-always-box",
              id: alwaysId,
              type: "checkbox",
              on: { change: (event) => { st.always = event.target.checked; } },
            }),
            "Don't ask again for ", h("code", { class: "ta-always-tool mono" }, call.tool)),
      h("div", { class: "ta-actions-right" },
        h("button", {
          class: "ta-deny",
          type: "button",
          on: { click: () => onRespond(false, false) },
        }, "Deny"),
        call.destructive
          ? HoldToAllow(call.id, () => onRespond(true, false))
          : h("button", {
              class: "ta-allow",
              type: "button",
              on: { click: () => onRespond(true, st.always) },
            }, "Allow"))));
  return card;
}

/* ---- StatusIcon: spinner while running, drawn check when done, X otherwise ---- */

function StatusIcon(state) {
  if (state === "approval-responded") {
    return s("svg", { "aria-hidden": "true", class: "ta-spin", fill: "none", viewBox: "0 0 16 16" },
      s("circle", { class: "ta-spin-track", cx: "8", cy: "8", r: "6.25", "stroke-width": "1.5" }),
      s("path", { class: "ta-spin-arc", d: "M8 1.75a6.25 6.25 0 0 1 6.25 6.25", "stroke-linecap": "round", "stroke-width": "1.5" }));
  }
  if (state === "output-available") {
    return s("svg", { "aria-hidden": "true", class: "ta-status-ok", fill: "none", viewBox: "0 0 16 16" },
      s("path", { class: "ta-draw", d: "M3.5 8.5 6.5 11.5 12.5 4.5", pathLength: "1", stroke: "currentColor", "stroke-linecap": "round", "stroke-linejoin": "round", "stroke-width": "1.75" }));
  }
  return s("svg", { "aria-hidden": "true", class: state === "output-error" ? "ta-status-bad" : "ta-status-dim", fill: "none", viewBox: "0 0 16 16" },
    s("path", { d: "M4.5 4.5l7 7m0-7-7 7", stroke: "currentColor", "stroke-linecap": "round", "stroke-width": "1.75" }));
}

/* ---- verbs: what the trace line says, as [while running, once done] ---- */

function verbsFor(tool) {
  const name = String(tool || "").toLowerCase();
  if (TERMINAL_TOOL.test(name)) return ["Running", "Ran"];
  if (FILE_TOOL.test(name)) return ["Editing", "Edited"];
  if (WEB_TOOL.test(name)) return ["Fetching", "Fetched"];
  if (SEND_TOOL.test(name)) return ["Sending", "Sent"];
  return ["Using", "Used"];
}

function verbOf(call) {
  const [running, done] = verbsFor(call.tool);
  switch (call.state) {
    case "approval-responded":
      return running;
    case "output-denied":
      return "Denied";
    case "output-error":
      return "Failed";
    default:
      return done;
  }
}

/* ---- TraceLine: a decided call, folded to one line; finished ones with
   output open it on click ---- */

function TraceLine(call) {
  const st = uiFor(call.id);
  const isRunning = call.state === "approval-responded";
  const hasOutput = Boolean(call.output?.length) && call.state !== "output-denied";
  const outputId = `ta-out-${call.id}`;

  const line = h("button", {
    "aria-controls": hasOutput ? outputId : undefined,
    "aria-expanded": hasOutput ? st.open : undefined,
    class: "ta-line" + (st.open && hasOutput ? " ta-line-open" : ""),
    disabled: !hasOutput || isRunning,
    type: "button",
    on: { click: () => { st.open = !st.open; } },
  },
    h("span", { class: "ta-status" }, StatusIcon(call.state)),
    h("span", { class: "ta-verb" + (call.state === "output-denied" ? " ta-verb-dim" : "") }, verbOf(call)),
    h("code", { class: "ta-code mono" + (call.state === "output-denied" ? " ta-code-dim" : "") }, inputSummary(call.input)),
    hasOutput && !isRunning ? lucideIcon("chevron-right") : null);

  const wrap = h("div", { class: "ta-line-wrap" }, line);
  if (hasOutput && (isRunning || st.open)) {
    wrap.append(h("div", { class: "ta-output-wrap" + (st.open ? " ta-open" : ""), id: outputId },
      h("pre", { class: "ta-output mono" }, call.output.join("\n"))));
  }
  return wrap;
}

/* ---- Our adapter: map a HUD permission request to the component's ToolCall ---- */

function toCall(r, settings) {
  const detail = String(r.detail ?? "");
  const protectedCommands = (settings && settings.protected_commands) || [];
  const lower = detail.toLowerCase();
  return {
    id: r.id,
    tool: r.tool,
    // The component's verb phrase for this tool.
    title: verbsFor(r.tool)[0],
    input: detail,
    reason: `${r.role || r.worker_id} · ${String(r.provider || "").toUpperCase()} · ${r.worker_id}`,
    rules: r.rules || [],
    destructive:
      r.destructive === true ||
      protectedCommands.some((c) => lower.includes(String(c).toLowerCase())),
    state: "approval-requested",
  };
}

function pruneAnswered(now) {
  for (const [id, entry] of answered) {
    if (now - entry.at > TRACE_MS) {
      answered.delete(id);
      uiState.delete(id);
    }
  }
}

/**
 * The permissions card. `requests` is the live list from /api/status;
 * `settings.protected_commands` (live settings) marks destructive calls.
 */
export function permissionsCard(requests, settings = {}) {
  const now = Date.now();
  pruneAnswered(now);

  const pending = (requests || []).map((r) => toCall(r, settings)).filter((c) => !answered.has(c.id));
  if (!pending.length && !answered.size) return null;

  const callsById = new Map((requests || []).map((r) => [r.id, r]));
  const decided = [...answered.values()].sort((a, b) => a.at - b.at);
  const current = pending[0];

  const respond = (call, approved, always) => {
    // Set when the user decides from the keyboard, so the next card takes
    // focus instead of it falling back to the page.
    refocusNext = Boolean(lastRoot?.contains(document.activeElement));
    const el = lastRoot?.querySelector(`[data-call-id="${CSS.escape(String(call.id))}"]`);
    if (el?.animate) {
      // AnimatePresence exit: opacity 0, y -6, blur 2px, 220ms EASE_OUT.
      el.animate(
        [
          { opacity: 1, transform: "translateY(0)", filter: "blur(0px)" },
          { opacity: 0, transform: "translateY(-6px)", filter: "blur(2px)" },
        ],
        { duration: 220, easing: EASE_OUT, fill: "forwards" });
    }
    const entry = { call: { ...call }, at: Date.now(), enter: true };
    entry.call.state = "approval-responded";
    answered.set(call.id, entry);
    const decision = approved ? (always ? "allow_always" : "allow") : "deny";
    act("/api/answer_permission", { id: call.id, decision })
      .then(() => {
        const e = answered.get(call.id);
        if (e) e.call.state = approved ? "output-available" : "output-denied";
      })
      .catch((err) => {
        const e = answered.get(call.id);
        if (e) {
          e.call.state = "output-error";
          e.call.output = [String((err && err.message) || err)];
        }
      });
  };

  const root = h("section", { class: "card", "aria-label": "Permission requests" },
    h("div", { class: "card-head" },
      h("h2", { class: "card-title" }, `${pending.length} permission request${pending.length === 1 ? "" : "s"} waiting`)),
    h("div", { class: "ta-root" },
      decided.length
        ? h("div", { class: "ta-traces" },
            decided.map((entry) => {
              const line = TraceLine(entry.call);
              if (entry.enter) line.classList.add("ta-enter");
              return line;
            }))
        : null,
      current
        ? h("div", { class: "ta-current" + (seenCards.has(current.id) ? "" : " ta-enter") },
            pending.length > 1
              ? h("p", { class: "ta-waiting" }, `1 of ${pending.length} waiting`)
              : null,
            ApprovalCard(current, (approved, always) => respond(current, approved, always)))
        : null));

  for (const entry of decided) entry.enter = false;
  seenCards.clear();
  if (current) seenCards.add(current.id);

  // Refocus the next card after a keyboard decision (AnimatePresence-free port).
  if (refocusNext && current) {
    requestAnimationFrame(() => {
      const focused = document.activeElement;
      if (focused && focused !== document.body && !root.contains(focused)) return;
      refocusNext = false;
      root.querySelector(`[data-call-id="${CSS.escape(String(current.id))}"]`)?.focus();
    });
  } else {
    refocusNext = false;
  }

  lastRoot = root;
  return root;
}
