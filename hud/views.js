/* One function per page. Each returns elements built from the latest status. */
import { h, api, act, ask, field, get, icon, modal, pct, providerColour, ticking, toast, tokens, trainMark } from "./lib.js";
import { lowPools, nextResets, providerCard, reachesLimit, spendChart } from "./usage.js";
import { sankeyChart, liveLineChart, bellButton, undoButton, notchGauge, barChart } from "./charts.js";
import { switchButton } from "./switch-button.js";
import { stackPanel } from "./stackpanel.js";
import { loadWake, wakeRuleRows, wakeStatusRows, wakeStatusNote } from "./wake.js";

export function renderSection(section) {
  return (section && section.rows) || [];
}

import { aiPrompt, isWorkerId, steerFromComposer } from "./ai-prompt.js";

let resetCycle = { index: 0, lastAdvanced: 0 };

const WORKER_STATE = {
  pending_approval: ["Awaiting approval", "accent"], launching: ["Launching", "accent"], running: ["Running", "accent"], stopping: ["Stopping", ""],
  completed: ["Completed", "ok"], failed: ["Failed", "bad"], timed_out: ["Timed out", "bad"], stopped: ["Stopped", ""], rejected: ["Rejected", ""],
  budget_stopped: ["Stopped at budget", "bad"],
};

/* ---- Permissions: the first thing on the page when an agent is waiting ---- */
import { permissionsCard } from "./tool-approval.js";
export { permissionsCard };

/* ---- Terminal commands: a mode of its own, plus the protected list ---- */
let terminalPanel = null;

export function terminalCommandsCard(settings) {
  const s = settings || {};
  const modes = s.permission_modes || {
    ask: "Approve everything",
    auto_safe: "Auto-approve safe commands",
    auto_approve: "Auto-approve everything",
  };
  const current = s.terminal_mode || "ask";
  const protectedCommands = Array.isArray(s.protected_commands) ? s.protected_commands : [];
  const exceptions = Array.isArray(s.protected_exceptions) ? s.protected_exceptions : [];
  const post = (body) => act("/api/settings", body);

  const choice = (id, label, selected, onSelect) => ({ id, label, selected: Boolean(selected), onSelect });

  const listSection = (title, hint, key, items, tone) => {
    const input = h("input", {
      type: "text",
      class: "sp-input",
      value: "",
      placeholder: tone === "allow" ? "e.g. git status" : "e.g. npm publish",
      "aria-label": `Add a command to ${title}`,
    });
    const add = () => {
      const value = input.value.trim();
      if (!value) return;
      if (items.includes(value)) { input.value = ""; return; }
      post({ [key]: [...items, value] });
    };
    const remove = (item) => post({ [key]: items.filter((value) => value !== item) });
    const form = h("form", { class: "rule-add", on: { submit: (e) => { e.preventDefault(); add(); } } },
      input,
      h("button", { class: "btn primary", type: "submit" }, "Add"));

    const rows = items.length
      ? items.map((item) => ({
          id: `${key}:${item}`,
          label: item,
          trailing: h("button", {
            class: "rule-remove btn",
            type: "button",
            "aria-label": `Remove ${item}`,
            title: `Remove ${item}`,
            on: { click: () => remove(item) },
          }, "×"),
        }))
      : [{
          id: `${key}:empty`,
          label: tone === "allow" ? "No exceptions." : "No protected commands.",
          detail: tone === "allow" ? "Every protected match still asks." : "Every command follows the mode above.",
        }];

    return [
      {
        rows,
        footer: hint,
      },
      {
        title: "Add command",
        rows: [{ id: `${key}:add`, label: "New command", trailing: form }],
      },
    ];
  };

  let confirming = false;
  const resetArea = h("span", { class: "sp-stepper" });
  const renderReset = () => {
    resetArea.replaceChildren();
    if (!confirming) {
      resetArea.append(h("button", {
        class: "btn",
        type: "button",
        on: { click: (e) => { e.stopPropagation(); confirming = true; renderReset(); } },
      }, "Reset"));
      return;
    }
    resetArea.append(
      h("button", {
        class: "btn primary",
        type: "button",
        on: { click: (e) => { e.stopPropagation(); confirming = false; post({ protected_reset: true }); renderReset(); } },
      }, "Confirm"),
      h("button", {
        class: "btn",
        type: "button",
        on: { click: (e) => { e.stopPropagation(); confirming = false; renderReset(); } },
      }, "Cancel"));
  };
  renderReset();

  const pages = {
    terminal: {
      title: "Terminal commands",
      sections: [
        {
          rows: [
            { id: "mode", label: "Mode", detail: modes[current] || current, to: "term_mode" },
            { id: "protected", label: "Protected commands", detail: String(protectedCommands.length), to: "term_protected" },
            { id: "exceptions", label: "Allowed without asking", detail: String(exceptions.length), to: "term_exceptions" },
            { id: "reset", label: "Reset to defaults", trailing: resetArea },
          ],
          footer: current === "auto_approve"
            ? "Terminal commands will run without asking, except protected commands."
            : "Separate from worker permissions. Protected commands below always ask.",
        },
      ],
    },
    term_mode: {
      title: "Mode",
      back: "terminal",
      sections: [
        {
          rows: Object.entries(modes).map(([key, label]) =>
            choice(key, label, key === current, () => post({ terminal_mode: key }))),
          footer: "Applies to every terminal command, except the protected ones below.",
        },
      ],
    },
    term_protected: {
      title: "Protected commands",
      back: "terminal",
      sections: listSection("Protected commands", "These always ask, in every mode. A match here overrides the mode above.", "protected_commands", protectedCommands, "protect"),
    },
    term_exceptions: {
      title: "Allowed without asking",
      back: "terminal",
      sections: listSection("Allowed without asking", "A command here skips its matching protected rule, never the others.", "protected_exceptions", exceptions, "allow"),
    },
  };

  if (terminalPanel) {
    terminalPanel.classList.add("terminal-panel");
    terminalPanel.__sp.update(pages);
    return terminalPanel;
  }
  terminalPanel = stackPanel({ pages, root: "terminal" });
  terminalPanel.classList.add("settings-panel", "terminal-panel");
  return terminalPanel;
}

/* Settings lives/* Settings lives in a stack panel (kokonutui/stack-panel): the root lists the
   groups, and each row drills into its own page. The panel element is cached so
   a re-render updates the values without losing the page you are on. */
let settingsPanel = null;
let currentSettingsPage = "settings";
let governorStatusCache = null;
let governorStatusLastFetch = 0;

export function settingsCard(settings, permissionCount = 0) {
  const s = settings || {};
  const cap = s.capacity || {};
  const loc = cap.local || {};
  const currentCompaction = s.compaction || "suggest";
  const currentCaffeinate = (s.caffeinate || "off").toLowerCase();
  const currentBranchMode = Boolean(s.git_branch_mode);
  const targetList = Array.isArray(s.notification_targets) ? s.notification_targets : ["ide", "desktop"];
  const has = (t) => targetList.includes(t);
  const withTarget = (t, on) => (on ? targetList.filter((x) => x !== t) : [...targetList, t]);
  const webhookUrl = s.notification_webhook || "";
  const openIn = s.hud_open_in || "auto";

  const save = (payload) => act("/api/settings", payload);

  /* A number with - / + that saves on every change. */
  const stepper = (value, placeholder, min, max, onChange) => {
    const current = () => (Number(value) || Number(placeholder) || min);
    return h("span", { class: "sp-stepper" },
      h("button", { type: "button", class: "sp-step", "aria-label": "Decrease", on: { click: () => onChange(Math.max(min, current() - 1)) } }, "\u2212"),
      h("input", {
        type: "number", class: "sp-num", min, max, inputmode: "numeric",
        "aria-label": "Value", value: value ?? "", placeholder: String(placeholder ?? ""),
        on: { change: (e) => { const v = parseInt(e.target.value, 10); onChange(Number.isFinite(v) && v > 0 ? v : 0); } },
      }),
      h("button", { type: "button", class: "sp-step", "aria-label": "Increase", on: { click: () => onChange(current() + 1) } }, "+"));
  };
  /* Like stepper but treats 0 as a real value (off). */
  const numField = (value, min, max, onChange) => {
    const cur = () => { const n = Number(value); return Number.isFinite(n) ? n : min; };
    return h("span", { class: "sp-stepper" },
      h("button", { type: "button", class: "sp-step", "aria-label": "Decrease", on: { click: () => onChange(Math.max(min, cur() - 1)) } }, "\u2212"),
      h("input", {
        type: "number", class: "sp-num", min, max, inputmode: "numeric",
        "aria-label": "Value", value: Number.isFinite(Number(value)) ? Number(value) : min,
        on: { change: (e) => { const v = parseInt(e.target.value, 10); onChange(Number.isFinite(v) ? Math.max(min, Math.min(max, v)) : min); } },
      }),
      h("button", { type: "button", class: "sp-step", "aria-label": "Increase", on: { click: () => onChange(Math.min(max, cur() + 1)) } }, "+"));
  };

  const choice = (id, label, selected, onSelect) => ({ id, label, selected: Boolean(selected), onSelect });
  const flag = (id, label, checked, onSelect, detail) => ({ id, label, checked: Boolean(checked), onSelect, detail });

  /* Archive retention. These keys were read by archive_maintain() but had no way to be
     set from anywhere, so the feature could not be turned on. */
  const archive = s.archive || {};
  const archiveChoices = Array.isArray(archive.choices) && archive.choices.length ? archive.choices : ["off", "3", "7", "30", "60", "90"];
  const archiveLabel = (v) => (v === "off" || v == null || v === "" ? "Off" : `${v} days`);
  const postArchive = (body) => act("/api/archive_settings", body);
  const archiveRows = (field, label) => archiveChoices.map((v) =>
    choice(`${field}:${v}`, archiveLabel(v), String(archive[field] ?? "off") === String(v),
      () => postArchive({ [field]: String(v) })));

  const wake = s.wake || {};
  const wakeChoices = Array.isArray(wake.choices) && wake.choices.length ? wake.choices : ["all", "outcomes", "failures", "off"];
  const wakeLabel = (v) => ({ all: "All", outcomes: "Outcomes", failures: "Failures", off: "Off" }[v] || (v ? String(v) : "All"));
  const postWake = (policy) => act("/api/wake_settings", { policy });

  const principal = s.principal || {};
  const principalGuard = principal.guard_enabled !== false;
  const principalReads = Number.isFinite(Number(principal.max_reads)) ? Math.max(0, Number(principal.max_reads)) : 30;
  const principalLabel = `${principalGuard ? "Edits blocked" : "Edits allowed"} \u00b7 ${principalReads > 0 ? principalReads + " reads" : "no read cap"}`;
  const postPrincipal = (body) => act("/api/principal_settings", body);
  const principalMcpReadTools = Array.isArray(principal.mcp_read_tools) ? principal.mcp_read_tools.join(", ") : "";

  const compactionLabel = { suggest: "Suggest", auto: "Auto", never: "Never" }[currentCompaction] || "Suggest";
  const caffeinateLabel = { off: "Off", workers: "When workers run", always: "Always" }[currentCaffeinate] || "Off";
  const tc = s.token_controls || {};
  const tcBudget = tc.read_budget || {};
  const currentBudgetReset = tcBudget.reset === "forever" ? "forever" : "compaction";
  const currentBudgetScope = tcBudget.scope === "provider" ? "provider" : "all";
  const resetModeLabel = currentBudgetReset === "forever" ? "Never" : "At compaction";
  const builderNudge = Number.isFinite(Number(tc.nudge_builder_calls)) ? Number(tc.nudge_builder_calls) : 5;
  const defaultNudge = Number.isFinite(Number(tc.nudge_default_calls)) ? Number(tc.nudge_default_calls) : 7;
  const hardStopNudge = Number.isFinite(Number(tc.no_edit_tool_calls)) ? Number(tc.no_edit_tool_calls) : 12;
  const tcGov = tc.governor || {};
  const govEnabled = tcGov.enabled !== false;
  const govLimit = Number.isFinite(Number(tcGov.probe_calls_per_turn)) ? Number(tcGov.probe_calls_per_turn) : 6;
  const govProviders = tcGov.providers || {};
  const provEnabled = (p) => govProviders[p] !== false;
  const formatGovSessions = (sessions) => {
    if (!sessions || !sessions.length) return "None";
    return sessions.map((sess) => `${sess.key || "session"}: ${sess.used ?? 0}/${sess.limit ?? 6}`).join(", ");
  };
  const govActiveList = h("span", { class: "sp-detail", "aria-live": "polite" }, formatGovSessions(governorStatusCache?.sessions));
  const openSpPage = settingsPanel && settingsPanel.__sp && typeof settingsPanel.__sp.stack === "function"
    ? (currentSettingsPage = settingsPanel.__sp.stack().slice(-1)[0] || currentSettingsPage)
    : currentSettingsPage;
  if (openSpPage === "tokens_governor") {
    const now = Date.now();
    if (now - governorStatusLastFetch > 10000) {
      governorStatusLastFetch = now;
      api("/api/governor_status").then((data) => {
        governorStatusCache = data;
        const sessions = (data && Array.isArray(data.sessions)) ? data.sessions : [];
        govActiveList.textContent = formatGovSessions(sessions);
      }).catch(() => {
        govActiveList.textContent = "None";
      });
    }
  }
  const openInLabel = {
    auto: "Automatic (browser)", browser: "System browser", app: "In-app browser",
    editor: "Any editor", antigravity: "Antigravity IDE", vscode: "VS Code",
    cursor: "Cursor", windsurf: "Windsurf",
  }[openIn] || "Automatic (browser)";

  const permModes = s.permission_modes || {};
  const permMode = s.permission_mode || "ask";
  const permLabel = permModes[permMode] || permMode;
  const autoApprove = permMode === "auto_approve";

  const REPLY_STYLES = [["explanatory", "Explanatory"], ["normal", "Normal"], ["concise", "Concise"]];
  const REPLY_PROVIDERS = [["claude", "Claude"], ["antigravity", "Antigravity"], ["opencode", "OpenCode"], ["pi", "pi"]];
  const replyDefault = (s.reply_style && s.reply_style.default) || "concise";
  const replyProv = (s.reply_style && s.reply_style.providers) || {};
  const replyLabel = (v) => (REPLY_STYLES.find(([k]) => k === v) || [])[1] || "";
  const pages = {
    settings: {
      title: "Settings",
      sections: [
        {
          rows: [
            { id: "permissions", label: "Permissions", detail: permLabel, tone: autoApprove ? "danger" : undefined, to: "permissions" },
            { id: "workers", label: "Workers", detail: `${s.god_mode ? "God mode" : "Standard"} \u00b7 ${s.max_workers || "Auto"} max`, to: "workers" },
            { id: "local", label: "Local models", detail: `${s.max_local_workers || "Auto"} max`, to: "local" },
            { id: "context", label: "Context", detail: compactionLabel, to: "context" },
            { id: "archive", label: "Archive", detail: `${archiveLabel(archive.compress_days)} compress \u00b7 ${archiveLabel(archive.delete_days)} delete \u00b7 ${archiveLabel(archive.branch_days)} branch`, to: "archive" },
            { id: "wake", label: "Wake-ups", detail: wakeLabel(wake.policy), to: "wake" },
            { id: "principal", label: "Principal", detail: principalLabel, to: "principal" },
          ],
        },
        {
          rows: [
            { id: "behaviour", label: "Behaviour", detail: caffeinateLabel, to: "behaviour" },
            { id: "reply", label: "Reply style", detail: ((s.reply_style && s.reply_style.default) || "concise").charAt(0).toUpperCase() + ((s.reply_style && s.reply_style.default) || "concise").slice(1), to: "reply" },
            { id: "tokens", label: "Token controls", detail: resetModeLabel, to: "tokens" },
            { id: "git", label: "Git", detail: currentBranchMode ? "Branch track on" : "Branch track off", to: "git" },
            { id: "notifications", label: "Notifications", detail: targetList.length ? targetList.join(", ") : "Off", to: "notifications" },
            { id: "startup", label: "Startup", detail: openInLabel, to: "startup" },
          ],
        },
      ],
    },
    permissions: {
      title: "Permissions",
      sections: [
        {
          rows: Object.entries(permModes).map(([key, label]) =>
            choice(key, label, key === permMode, () => save({ permission_mode: key }))),
          footer: autoApprove
            ? "Workers will not ask before running commands. A chat can override this for itself on the Threads page."
            : "How spun agents get permission to act. A chat can override this for itself on the Threads page.",
        },
      ],
    },
    workers: {
      title: "Workers",
      sections: [
        {
          rows: [
            flag("god", "God mode", s.god_mode, (v) => save({ god_mode: !s.god_mode }), "No permission prompts for workers."),
            flag("limits", "Worker limits", s.worker_limits !== false, () => save({ worker_limits: s.worker_limits === false }), "Cap how many run at once."),
          ],
        },
        {
          rows: [{ id: "max", label: "Max workers", trailing: stepper(s.max_workers, cap.auto_limit || "Auto", 1, 128, (v) => save({ max_workers: v })) }],
          footer: `${cap.auto_limit || cap.machine || "Auto"} suggested with ${cap.free_ram_gb || "?"} GB RAM free. Leave empty for the automatic limit.`,
        },
      ],
    },
    local: {
      title: "Local models",
      sections: [
        {
          rows: [
            flag("guard", "Memory guard", s.local_memory_guard !== false, () => save({ local_memory_guard: s.local_memory_guard === false }), "Keep local models inside free RAM."),
            flag("conc", "Concurrency", s.local_concurrency_limits !== false, () => save({ local_concurrency_limits: s.local_concurrency_limits === false }), "Cap parallel local runs."),
          ],
        },
        {
          rows: [{ id: "maxlocal", label: "Max local workers", trailing: stepper(s.max_local_workers, loc.auto_local_workers || "Auto", 1, 32, (v) => save({ max_local_workers: v })) }],
          footer: `Fits about ${loc.auto_local_workers || 1} instance(s)${loc.model_needs_gb ? ` (~${loc.model_needs_gb} GB per model)` : ""} in ${loc.safe_room_gb || cap.free_ram_gb || "?"} GB.`,
        },
      ],
    },
    context: {
      title: "Context",
      sections: [
        {
          title: "When a chat gets expensive",
          rows: [
            choice("suggest", "Suggest", currentCompaction === "suggest", () => save({ compaction: "suggest" })),
            choice("auto", "Auto", currentCompaction === "auto", () => save({ compaction: "auto", compact_at_tokens: Number(s.compact_at_tokens) || 300000 })),
            choice("never", "Never", currentCompaction === "never", () => save({ compaction: "never" })),
          ],
          footer: {
            suggest: "Warns once, with a ready /compact line. You decide.",
            auto: "Warns, and compacts OpenCode sessions itself.",
            never: "No warnings.",
          }[currentCompaction],
        },
        {
          rows: [{ id: "at", label: "Compact at", trailing: stepper(s.compact_at_tokens != null && s.compact_at_tokens !== "" ? s.compact_at_tokens : 300000, 300000, 100000, 1000000, (v) => save({ compaction: currentCompaction, compact_at_tokens: v })) }],
          footer: "Tokens, checked on each turn.",
        },
      ],
    },
    archive: {
      title: "Archive",
      sections: [
        {
          title: "Compress archived workers after",
          rows: archiveRows("compress_days", "Compress after"),
          footer: "Old worker records are compressed in place. Active workers are never touched.",
        },
        {
          title: "Delete archived workers after",
          rows: archiveRows("delete_days", "Delete after"),
          footer: "Off keeps them forever. Deleting is permanent.",
        },
        {
          title: "Delete finished worker branches after",
          rows: archiveRows("branch_days", "Delete branches after"),
          footer: "Removes temporary worker git branches and detached worktrees after retention window.",
        },
        {
          rows: [{ id: "maintain", label: "Run cleanup now", trailing: h("button", {
            class: "btn", type: "button",
            on: { click: () => act("/api/archive_settings", { action: "maintain" }) },
          }, "Run") }],
          footer: "Applies both rules once, right now.",
        },
      ],
    },
    wake: (() => {
      if (openSpPage === "wake") {
        loadWake(() => document.dispatchEvent(new Event("subway:rerender")));
      }
      const statusRows = wakeStatusRows();
      return {
        title: "Wake-ups",
        sections: [
          {
            rows: [
              choice("all", "All", (wake.policy || "all") === "all", () => postWake("all")),
              choice("outcomes", "Outcomes", wake.policy === "outcomes", () => postWake("outcomes")),
              choice("failures", "Failures", wake.policy === "failures", () => postWake("failures")),
              choice("off", "Off", wake.policy === "off", () => postWake("off")),
            ],
            footer: "Off silences all wake-ups except permission requests.",
          },
          {
            title: "Rules",
            rows: wakeRuleRows(flag, (key, on) => act("/api/wake_rules", { rules: { [key]: { wake: on } } })),
            footer: "What wakes a principal. Provider and chat overrides win over these.",
          },
          {
            title: "Status",
            rows: statusRows,
            footer: statusRows.note || wakeStatusNote(),
          },
        ],
      };
    })(),
    principal: {
      title: "Principal",
      sections: [
        {
          rows: [
            flag("guard", "Block principal edits", principalGuard, () => postPrincipal({ guard_enabled: !principalGuard })),
            { id: "reads", label: "Read budget per session", trailing: numField(principalReads, 0, 9999, (v) => postPrincipal({ max_reads: v })) },
          ],
          footer: "When enabled, the principal chat cannot edit project files and is limited to this many read/search/shell calls per session. 0 disables the read budget. Workers are never limited.",
        },
        {
          rows: [{
            id: "mcp_read_tools", label: "MCP tools counted as reads",
            trailing: h("input", {
              type: "text", class: "sp-input", value: principalMcpReadTools,
              placeholder: "mcp__subway__read_symbol, ...",
              "aria-label": "MCP tools counted as reads",
              on: { change: (e) => postPrincipal({ mcp_read_tools: e.target.value }) },
            }),
          }],
          footer: "Comma-separated MCP tool names that count against the read budget above when called from the principal chat (default: read_symbol, context_slice, read_terminal, search_terminal, project_files). Coordination tools and worker code packets never count.",
        },
      ],
    },
    behaviour: {
      title: "Behaviour",
      sections: [
        {
          title: "Stay awake",
          rows: [
            choice("off", "Off", currentCaffeinate === "off", () => save({ caffeinate: "off" })),
            choice("workers", "When workers run", currentCaffeinate === "workers", () => save({ caffeinate: "workers" })),
            choice("always", "Always", currentCaffeinate === "always", () => save({ caffeinate: "always" })),
          ],
          footer: "Keeps the Mac awake while work is in flight.",
        },
      ],
    },
    reply: {
      title: "Reply style",
      back: "settings",
      sections: [
        {
          title: "All principals",
          rows: REPLY_STYLES.map(([v, l]) => choice(`reply-${v}`, l, replyDefault === v, () => save({ reply_style: v }))),
          footer: "Shapes replies only; thinking is never limited. Applies to new chats or after /mcp reconnect.",
        },
        {
          title: "Per provider",
          rows: REPLY_PROVIDERS.map(([p, l]) => ({ id: `reply-p-${p}`, label: l, detail: replyLabel(replyProv[p]) || "Default", to: `reply-${p}` })),
        },
      ],
    },
    ...Object.fromEntries(REPLY_PROVIDERS.map(([p, l]) => [`reply-${p}`, {
      title: l,
      back: "reply",
      sections: [{
        title: "Reply style",
        rows: [["", "Default (all principals)"], ...REPLY_STYLES].map(([v, lab]) =>
          choice(`reply-${p}-${v || "default"}`, lab, (replyProv[p] || "") === v, () => save({ reply_style_provider: p, reply_style_value: v }))),
      }],
    }])),
    tokens: {
      title: "Token controls",
      sections: [
        {
          rows: [
            { id: "governor", label: "Delegation governor", detail: `${govEnabled ? "On" : "Off"} \u00b7 ${govLimit} per turn`, to: "tokens_governor" },
            { id: "reads", label: "Read limit", detail: resetModeLabel, to: "tokens_reads" },
            { id: "nudges", label: "Nudges", detail: `${builderNudge} builders \u00b7 ${defaultNudge} others`, to: "tokens_nudges" },
          ],
        },
      ],
    },
    tokens_governor: {
      title: "Delegation governor",
      back: "tokens",
      sections: [
        {
          title: "Delegation governor",
          rows: [
            flag("gov_enabled", "Delegation governor", govEnabled, () => save({ governor_enabled: !govEnabled })),
            { id: "gov_limit", label: "Probe calls per turn", trailing: stepper(govLimit, 6, 2, 30, (v) => save({ governor_limit: Math.max(2, Math.min(30, v)) })) },
            flag("gov_claude", "Claude", provEnabled("claude"), () => save({ governor_provider: "claude", governor_provider_on: !provEnabled("claude") })),
            flag("gov_antigravity", "Antigravity", provEnabled("antigravity"), () => save({ governor_provider: "antigravity", governor_provider_on: !provEnabled("antigravity") })),
            flag("gov_opencode", "OpenCode", provEnabled("opencode"), () => save({ governor_provider: "opencode", governor_provider_on: !provEnabled("opencode") })),
            flag("gov_pi", "Pi", provEnabled("pi"), () => save({ governor_provider: "pi", governor_provider_on: !provEnabled("pi") })),
            { id: "gov_active", label: "Active now", trailing: govActiveList },
          ],
          footer: "After this many reads or shell commands in one turn, a principal must delegate with spin_worker. Spinning, approving, status and landing still work.",
        },
      ],
    },
    tokens_reads: {
      title: "Read limit",
      back: "tokens",
      sections: [
        {
          title: "Read limit resets",
          rows: [
            choice("compaction", "At compaction", currentBudgetReset === "compaction", () => save({ read_budget_reset: "compaction" })),
            choice("forever", "Never", currentBudgetReset === "forever", () => save({ read_budget_reset: "forever" })),
          ],
          footer: "The principal's read count (limit 200) clears at this point.",
        },
        {
          title: "Applies to",
          rows: [
            choice("all", "All chats", currentBudgetScope === "all", () => save({ read_budget_scope: "all" })),
            choice("provider", "This provider only", currentBudgetScope === "provider", () => save({ read_budget_scope: "provider" })),
          ],
        },
        {
          title: "Reset now",
          rows: [
            {
              id: "reset_now",
              label: "Reset read count now",
              trailing: h("button", {
                class: "btn",
                type: "button",
                on: { click: () => save({ read_budget_reset_now: true }) },
              }, "Reset"),
            },
          ],
        },
      ],
    },
    tokens_nudges: {
      title: "Nudges",
      back: "tokens",
      sections: [
        {
          title: "Nudges",
          rows: [
            { id: "builder", label: "Builder", trailing: stepper(builderNudge, 5, 2, 50, (v) => save({ nudge_builder_calls: Math.max(2, Math.min(50, v)) })) },
            { id: "default", label: "Other workers", trailing: stepper(defaultNudge, 7, 2, 50, (v) => save({ nudge_default_calls: Math.max(2, Math.min(50, v)) })) },
            { id: "hard_stop", label: "Hard stop", trailing: stepper(hardStopNudge, 12, 2, 50, (v) => save({ no_edit_tool_calls: Math.max(2, Math.min(50, v)) })) },
          ],
        },
      ],
    },
    git: {
      title: "Git",
      sections: [
        {
          rows: [flag("branch", "Branch track mode", currentBranchMode, () => save({ git_branch_mode: !currentBranchMode }))],
          footer: "Named branches and merge commits, so isolated workers render as a parallel tree.",
        },
      ],
    },
    notifications: {
      title: "Notifications",
      sections: [
        {
          title: "Channels",
          rows: [
            flag("ide", "IDE", has("ide"), () => save({ notification_targets: withTarget("ide", has("ide")) })),
            flag("desktop", "Desktop", has("desktop"), () => save({ notification_targets: withTarget("desktop", has("desktop")) })),
            flag("agent", "Webhook", has("agent"), () => save({ notification_targets: withTarget("agent", has("agent")) })),
          ],
          footer: "Where notifications are delivered.",
        },
        ...(has("agent") ? [{
          rows: [{
            id: "url", label: "Webhook URL",
            trailing: h("input", {
              type: "text", class: "sp-input", value: webhookUrl, placeholder: "https://...",
              "aria-label": "Webhook URL",
              on: { change: (e) => save({ notification_webhook: e.target.value }) },
            }),
          }],
        }] : []),
        {
          rows: [{ id: "test", label: "Send a test alert", trailing: bellButton({ count: permissionCount, active: true, title: "Send test alert", onClick: () => act("/api/notify_test", {}) }) }],
        },
      ],
    },
    startup: {
      title: "Startup",
      sections: [
        {
          rows: [
            flag("autostart", "Start the HUD with Subway", s.hud_autostart, (v) => save({ hud_autostart: !s.hud_autostart })),
            flag("onuse", "Open the HUD when Subway is used", s.hud_open_on_use, (v) => save({ hud_open_on_use: !s.hud_open_on_use })),
          ],
          footer: "Starts and opens the dashboard around a chat that uses Subway.",
        },
        {
          rows: [{ id: "openin", label: "Where the HUD opens", detail: openInLabel, to: "openin" }],
        },
      ],
    },
    openin: {
      title: "Where the HUD opens",
      sections: [
        {
          rows: [
            choice("auto", "Automatic (browser)", openIn === "auto", () => save({ hud_open_in: "auto" })),
            choice("browser", "System browser", openIn === "browser", () => save({ hud_open_in: "browser" })),
            choice("app", "In-app browser (DeepAI Code)", openIn === "app", () => save({ hud_open_in: "app" })),
            choice("editor", "Any editor with the extension", openIn === "editor", () => save({ hud_open_in: "editor" })),
            choice("antigravity", "Antigravity IDE", openIn === "antigravity", () => save({ hud_open_in: "antigravity" })),
            choice("vscode", "VS Code", openIn === "vscode", () => save({ hud_open_in: "vscode" })),
            choice("cursor", "Cursor", openIn === "cursor", () => save({ hud_open_in: "cursor" })),
            choice("windsurf", "Windsurf", openIn === "windsurf", () => save({ hud_open_in: "windsurf" })),
          ],
          footer: "Automatic only uses the editor Subway is running inside; it never picks a different installed one.",
        },
      ],
    },
  };

  if (settingsPanel) {
    settingsPanel.__sp.update(pages);
    return settingsPanel;
  }
  settingsPanel = stackPanel({ pages, root: currentSettingsPage || "settings" });
  settingsPanel.classList.add("settings-panel");
  return settingsPanel;
}

/* ---- Chat Context Card ---- */
export function chatContextCard(ctx) {
  if (!ctx || ctx.pct == null) return null;
  const segmentsTag = (ctx.segments && ctx.segments > 1)
    ? h("span", { class: "tag" }, `${ctx.segments} segments`)
    : null;
  const pctNum = Math.min(100, Math.max(0, Number(ctx.pct) || 0));
  const tokenStr = (ctx.tokens != null && ctx.window != null)
    ? `${tokens(ctx.tokens)} of ${tokens(ctx.window)}`
    : "";
  const barClass = pctNum >= 90 ? "bar-fill bad" : (pctNum >= 75 ? "bar-fill accent" : "bar-fill");

  return h("section", { class: "card", "aria-labelledby": "chat-context-title" },
    h("div", { class: "card-head" },
      h("h2", { class: "card-title", id: "chat-context-title" }, "This chat"),
      segmentsTag ? h("div", { class: "tags" }, segmentsTag) : null),
    h("div", { class: "context-gauge" },
      h("div", { class: "context-gauge-head" },
        h("span", { class: "context-gauge-pct" }, `${ctx.pct}%`),
        tokenStr ? h("span", { class: "context-gauge-tokens" }, tokenStr) : null),
      h("div", { class: "bar", role: "progressbar", "aria-valuenow": String(pctNum), "aria-valuemin": "0", "aria-valuemax": "100" },
        h("div", { class: barClass, style: `width: ${pctNum}%` }))));
}

export function stationCard(s) {
  if (!s || !s.station_id) {
    return h("section", { class: "card station-card", "aria-labelledby": "station-card-title" },
      h("div", { class: "card-head" },
        h("h2", { class: "card-title", id: "station-card-title" }, "Subway Station")),
      h("div", { class: "empty" }, s && s.error ? `Station identity error: ${s.error}` : "Station identity unavailable."));
  }
  const dev = s.device || {};
  const routes = (s.routes || []).map((r) => h("span", { class: "tag mono" }, r));
  const providers = (s.providers || []).map((p) => h("span", { class: "tag" }, p));
  const ramStr = dev.total_ram_gb ? `${dev.free_ram_gb || 0} GB free / ${dev.total_ram_gb} GB` : "";

  function capacitySummary(cap) {
    if (!cap || typeof cap !== "object") return h("div", { class: "card-sub" }, "Capacity: unavailable");
    const loc = cap.local || {};
    const rows = [
      ["Max workers", Number.isFinite(cap.machine) ? cap.machine : "unavailable"],
      ["Free RAM", typeof cap.free_ram_gb === "number" ? `${cap.free_ram_gb} GB` : "unavailable"],
      ["Local model", loc.model_name || "unavailable"],
      ["Local workers that fit", Number.isFinite(loc.effective_local_workers) ? loc.effective_local_workers : "unavailable"],
    ];
    return h("div", { class: "capacity-summary" }, rows.map(([k, v]) =>
      h("div", { class: "legend-row" }, h("span", { class: "legend-key" }, k), h("span", { class: "legend-val" }, String(v)))));
  }

  return h("section", { class: "card station-card", "aria-labelledby": "station-card-title" },
    h("div", { class: "card-head" },
      h("h2", { class: "card-title", id: "station-card-title" }, "Subway Station"),
      h("div", { class: "tags" },
        h("span", { class: "tag mono bold" }, s.station_id),
        s.person ? h("span", { class: "tag" }, s.person) : null)),
    h("div", { class: "rows" },
      h("div", { class: "row", style: "display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px" },
        h("div", {},
          h("div", { class: "row-title" }, `${dev.hostname || "localhost"} (${dev.os || dev.platform || "host"} ${dev.arch || ""})`),
          ramStr ? h("div", { class: "row-sub" }, `RAM: ${ramStr}`) : null),
        h("div", { style: "display:flex;gap:6px;align-items:center;flex-wrap:wrap" },
          providers.length ? h("div", { class: "tags" }, providers) : null,
          s.models_count ? h("span", { class: "tag" }, `${s.models_count} models`) : null)),
      capacitySummary(s.capacity),
      routes.length ? h("div", { class: "row", style: "display:flex;gap:8px;align-items:center;padding-top:4px" },
        h("span", { class: "label", style: "margin:0" }, "Routes:"),
        h("div", { class: "tags" }, routes)) : null));
}

/* ---- Overview ---- */
export function budgetsCard(data) {
  const budgets = Array.isArray(data) ? data : data?.settings?.budgets;
  if (!budgets || !budgets.length) return null;

  const items = budgets.map((b) => {
    const scope = b.scope || "";
    const scopeKey = String(scope).toLowerCase();
    const displayName = (() => {
      const names = { claude: "Claude", antigravity: "Antigravity", opencode: "OpenCode", copilot: "Copilot", pi: "Pi", local: "Local", custom: "Custom", chat: "Chat", deepai: "DeepAI" };
      if (names[scopeKey]) return names[scopeKey];
      return scope ? scope.charAt(0).toUpperCase() + scope.slice(1) : "Budget";
    })();
    /* Use the same colour as this provider's Token Spend line (providerColour); scopes without a
       provider mapping (e.g. chat) get providerColour's fallback. */
    const color = providerColour(scope);

    let tag = null;
    if (b.status === "warning") {
      tag = h("span", { class: "tag accent" }, "Almost gone");
    } else if (b.status === "reached") {
      tag = h("span", { class: "tag bad" }, "Reached, work stopped");
    }

    const hasLimit = b.limit && b.limit > 0;
    const pctVal = hasLimit
      ? Math.min(100, Math.max(0, b.pct != null ? b.pct : (b.used != null ? ((b.used / b.limit) * 100) : 0)))
      : (b.pct != null ? Math.min(100, Math.max(0, b.pct)) : 0);
    const used = b.used != null ? b.used : null;
    const resetAt = b.reset_at || b.resetAt || b.resets_at || b.resetsAt || b.reset || b.reset_time || b.resetTime || "";
    const period = b.period || b.label || b.window || "";
    let detail = "No limit set";
    if (hasLimit && used != null) detail = `${tokens(used)} of ${tokens(b.limit)} tokens`;
    else if (hasLimit) detail = `Limit: ${tokens(b.limit)} tokens`;
    if (resetAt) detail += ` · resets ${resetAt}`;

    /* Compact gauge: centre shows the big percentage; caption is the word "used" (never the number). */
    const gauge = notchGauge({
      value: pctVal, max: 100, label: displayName, caption: "used",
      size: 124, radius: 50, thickness: 10, from: color, to: color,
      format: (v) => Math.round(v) + "%",
    });

    const input = h("input", {
      type: "number",
      min: 0,
      placeholder: "Tokens",
      value: hasLimit ? b.limit : "",
    });

    let btn;
    const setBudget = async () => {
      const raw = input.value.trim();
      if (!raw || !/^\d+$/.test(raw)) {
        toast("Enter a token number");
        return;
      }
      const val = parseInt(raw, 10);
      if (btn) btn.disabled = true;
      input.disabled = true;
      try {
        const res = await api("/api/budget", { scope: b.scope, tokens: val });
        toast(res.result || `Budget for ${b.scope} set`);
        document.dispatchEvent(new Event("subway:refresh"));
      } catch (e) {
        toast(e.message);
      } finally {
        if (btn) btn.disabled = false;
        input.disabled = false;
      }
    };

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        setBudget();
      }
    });

    btn = h("button", {
      class: "btn primary",
      type: "button",
      "aria-label": `Set ${displayName} budget`,
      on: { click: setBudget },
    }, "Set");

    /* Text column sits in the gauge's open side: bold name, swatch + period (or the detail when
       there is no period), then the compact form right under the name, then any warning tag. */
    const swatch = h("span", { class: "budget-swatch", style: `background:${color}`, "aria-hidden": "true" });
    const periodEl = period
      ? h("div", { class: "budget-period" }, swatch, period)
      : h("div", { class: "budget-period" }, swatch, detail);
    const text = h("div", { class: "budget-text" },
      h("div", { class: "budget-head" }, h("div", { class: "budget-name" }, displayName), periodEl),
      period ? h("div", { class: "budget-detail" }, detail) : null,
      h("div", { class: "budget-form" }, input, btn),
      tag ? h("div", { class: "tags budget-tags" }, tag) : null);

    return h("div", { class: "budget-gauge-item" },
      h("div", { class: "budget-gauge-wrap" }, gauge),
      text);
  });

  return h("section", { class: "card", "aria-labelledby": "budgets-title" },
    h("div", { class: "card-head" },
      h("h2", { class: "card-title", id: "budgets-title" }, "Budgets")),
    h("div", { class: "budget-gauges" }, items));
}

export function overview(d, link) {
  const now = d.timestamp;
  const providers = d.quotas.providers;
  const workers = d.workers || [];
  const pending = workers.filter((w) => w.status === "pending_approval").length;
  const running = workers.filter((w) => w.status === "running").length;
  const waiting = (d.permissions || []).length;
  const resets = nextResets(providers, now);
  const low = [];
  for (const p of providers) {
    const pools = lowPools(p, now);
    if (pools.length) {
      const named = pools.filter(Boolean);
      low.push(named.length ? `${p.name} (${named.join(", ")})` : p.name);
    }
  }
  const manualCount = providers.filter((p) => p.manual).length;
  const needs = waiting + pending;
  const headline = needs ? `${needs} thing${needs > 1 ? "s" : ""} waiting on you` : running ? `${running} worker${running > 1 ? "s" : ""} running` : "All clear";
  const tokens5h = providers.reduce((sum, p) => {
    const t = p.tokens;
    if (!t || typeof t !== "object") return sum;  /* some providers report a scalar total, not windows */
    const w = t["5h"];
    return sum + (w ? (w.input || 0) + (w.output || 0) : 0);
  }, 0);

  let resetSide = null;
  if (resets.length) {
    const nowMs = Date.now();
    if (!resetCycle.lastAdvanced) {
      resetCycle.lastAdvanced = nowMs;
    } else {
      const elapsed = nowMs - resetCycle.lastAdvanced;
      if (elapsed >= 5000) {
        const steps = Math.floor(elapsed / 5000);
        resetCycle.index = (resetCycle.index + steps) % resets.length;
        resetCycle.lastAdvanced += steps * 5000;
      }
    }
    const idx = resetCycle.index % resets.length;
    const cur = resets[idx];
    const counter = resets.length > 1 ? ` (${idx + 1} of ${resets.length})` : "";
    const fig = ticking("div", { class: "hero-figure" }, cur.at, "countdown");
    const note = h("div", { class: "hero-note" }, cur.label);
    const label = h("div", { class: "label" }, `Next limit reset${counter}`);
    resetSide = h("div", { class: "hero-side" }, label, fig, note);
    if (resets.length > 1) {
      const timer = setInterval(() => {
        if (!resetSide.isConnected) {
          clearInterval(timer);
          return;
        }
        resetCycle.index = (resetCycle.index + 1) % resets.length;
        resetCycle.lastAdvanced = Date.now();
        const nextCur = resets[resetCycle.index];
        fig.dataset.epoch = nextCur.at;
        note.textContent = nextCur.label;
        label.textContent = `Next limit reset (${resetCycle.index + 1} of ${resets.length})`;
      }, 5000);
    }
  }

  const kpi = (name, label, value) => h("div", { class: "kpi" }, h("div", { class: "kpi-top" }, icon(name), label), h("div", { class: "kpi-value" }, value));
  const actRows = terrainState.rows || [];
  const actCount = (r) => Number(r && (r.spun ?? r.count ?? r.workers ?? r.value ?? r.total) || 0);
  const today = actRows.length ? actCount(actRows[actRows.length - 1]) : 0;
  const year = actRows.reduce((s, r) => s + actCount(r), 0);
  const activityLine = actRows.length ? `${today} worker${today === 1 ? "" : "s"} today · ${year} this year` : "";
  return [
    permissionsCard(d.permissions || [], d.settings || {}),
    h("section", { class: "hero" },
      h("div", {}, h("div", { class: "hero-kicker" }, `${providers.length} LLM tool${providers.length === 1 ? "" : "s"} detected`),
        h("div", { class: "hero-head" }, headline),
        h("div", { class: "hero-note" }, low.length ? `Under 10% left: ${low.join(", ")}` : "No limit is under 10%."),
        activityLine ? h("div", { class: "hero-note" }, activityLine) : null,
        manualCount ? h("div", { class: "hero-note" }, `${manualCount} set by hand`) : null),
      resetSide),
    stationCard(d.station),
    h("div", { class: "kpis" },
      kpi("workers", "Running workers", running), kpi("bell", "Waiting on you", needs),
      kpi("leases", "File leases", Object.keys(d.locks || {}).length), kpi("bolt", "Tokens, last 5h", tokens(tokens5h))),
    chatContextCard(d.chat_context),
    budgetsCard(d),
    h("section", { class: "card" },
      h("h2", { class: "card-title" }, "Limits at a glance"),
      h("div", { class: "rows" }, compactLimits(providers, now))),
    d.version ? h("footer", { class: "ov-version" }, `Subway v${d.version}`) : null,
  ];
}

function compactLimits(providers, now) {
  const rows = [];
  for (const p of providers) {
    for (const pool of p.pools || []) {
      const live = pool.windows.filter((w) => !w.disabled && typeof w.left_pct === "number" && !(w.resets_at && w.resets_at <= now));
      if (!live.length) continue;
      rows.push(h("div", { class: "legend-row" },
        h("span", { class: "legend-key wrap" }, p.pools.length > 1 ? `${p.name} · ${pool.name}` : p.name),
        h("span", { class: "legend-val" }, live.map((w) => `${w.key === "5h" ? "5h" : "Wk"} ${pct(Math.round(w.left_pct))}`).join("  "))));
    }
  }
  return rows.length ? rows : h("div", { class: "empty" }, "No tool has reported a limit.");
}

/* ---- Usage ---- */
export function activityCard(data) {
  const items = Array.isArray(data)
    ? data
    : (data && Array.isArray(data.activity) ? data.activity : (terrainState.rows || []));
  const err = data && data.error ? data.error : (terrainState.error || "");

  let body;
  if (err) {
    body = h("div", { class: "empty activity-error" }, `Worker activity error: ${err}`);
  } else if (!items.length) {
    body = h("div", { class: "empty activity-empty" }, "No worker activity recorded in this period.");
  } else {
    const rows = items.map((r) => h("div", { class: "row activity-row" },
      h("div", { class: "activity-col-date mono bold" }, r.day || "Unknown date"),
      h("div", { class: "tags activity-tags" },
        h("span", { class: "tag" }, `${r.spun || 0} spun`),
        h("span", { class: "tag ok" }, `${r.finished || 0} finished`),
        h("span", { class: "tag accent mono" }, `${tokens(r.tokens || 0)} tokens`))));
    body = pagedRegion("worker-activity", "Worker activity history", rows);
  }

  return h("section", { class: "card activity-card", "aria-labelledby": "activity-card-title" },
    h("div", { class: "card-head" },
      h("h2", { class: "card-title", id: "activity-card-title" }, "Worker Activity"),
      items.length ? h("span", { class: "card-sub" }, `${items.length} day${items.length === 1 ? "" : "s"} recorded`) : null),
    body);
}

export function inlineEstimateCard(d) {
  const days = d && d.days && typeof d.days === "object" ? d.days : {};
  const entries = Object.entries(days).sort(([a], [b]) => b.localeCompare(a)).slice(0, 7);
  const rows = entries.map(([day, files]) => {
    const fileEntries = Object.entries(files || {}).filter(([_, v]) => Number(v) > 0);
    const total = fileEntries.reduce((n, [, v]) => n + Number(v || 0), 0);
    const fileTags = fileEntries.slice(0, 5).map(([f, count]) => {
      const base = f.split("/").pop() || f;
      return h("span", { class: "tag mono", title: `${f} (${count})` }, `${base}: ${count}`);
    });
    return h("div", { class: "row" },
      h("div", { class: "row-title mono" }, day),
      h("div", { class: "tags" },
        h("span", { class: "tag" }, `${total} est.`),
        ...fileTags
      )
    );
  });
  return h("section", { class: "card", "aria-labelledby": "inline-est-title" },
    h("div", { class: "card-head" },
      h("h2", { class: "card-title", id: "inline-est-title" }, "Inline suggestions (estimate)"),
      h("span", { class: "card-sub" }, "local heuristic, not Copilot telemetry")),
    rows.length ? rows : h("div", { class: "empty" }, "No inline suggestions estimated yet (VS Code)."));
}

/* Providers with live data first; installed-only and missing ones go below. */
const PROVIDER_RANK = { live: 0, stale: 1, detected: 2, unavailable: 3 };

export function usagePage(d) {
  return [
    inlineEstimateCard(d?.copilot_inline_estimate || {}),
    h("div", { class: "providers" }, d?.quotas?.providers?.length
      ? [...d.quotas.providers].sort((a, b) => (PROVIDER_RANK[a.state] ?? 2) - (PROVIDER_RANK[b.state] ?? 2)).map((p) => providerCard(p, d?.timestamp))
      : h("div", { class: "empty" }, "No LLM tools found on this machine."))
  ];
}

/* ---- Workers ---- */
/* ---- Worker cards --------------------------------------------------------
   One card per worker in the boarding-pass shape: a stub that is always
   visible (title, provider -> model route, status pill, headline facts and
   the actions) and, below the perforation, a tear-off that opens to the full
   detail.

   The previous layout sat the text beside the tag cluster in a
   `minmax(0, 1fr) auto` grid. A grid `auto` track is sized to its content's
   max-content width, and .tags / .btns never report their *wrapped* width, so
   a worker carrying many tags claimed the whole row and the neighbouring
   text track collapsed to 0px -- which, with overflow-wrap: anywhere on
   .row-title, rendered the title one character per line. Nothing here puts
   text beside a content-sized track: the stub is flex with min-width: 0 on
   the growing half, and the tags live in their own full-width rows. */

const WORKER_TONE = {
  running: "run", launching: "run", stopping: "run",
  completed: "ok",
  failed: "bad", timed_out: "bad", stopped: "bad", budget_stopped: "bad", rejected: "bad",
  pending_approval: "wait",
};

/* Format durations: <60s -> "42s", <1h -> "3m 12s", otherwise "1h 05m" */
export function fmtDuration(ms) {
  if (ms == null || isNaN(ms)) return "";
  const sec = Math.max(0, Math.floor(Number(ms) / 1000));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) {
    return `${m}m ${String(s).padStart(2, "0")}s`;
  }
  const h = Math.floor(m / 60);
  const remM = m % 60;
  return `${h}h ${String(remM).padStart(2, "0")}m`;
}

/* Format clocks: HH:MM if today, otherwise D Mon HH:MM */
export function fmtClock(iso) {
  if (!iso) return "";
  const d = (iso instanceof Date) ? iso : (typeof iso === "number" && iso < 1e11 ? new Date(iso * 1000) : new Date(iso));
  if (isNaN(d.getTime())) return "";
  const now = new Date();
  const isToday = d.getFullYear() === now.getFullYear() &&
                  d.getMonth() === now.getMonth() &&
                  d.getDate() === now.getDate();
  const pad = (n) => String(n).padStart(2, "0");
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (isToday) return time;
  const mon = d.toLocaleString(undefined, { month: "short" });
  return `${d.getDate()} ${mon} ${time}`;
}

/* Muted label + strong value pairs, in the order a reader wants them. Every
   entry is skipped when the worker does not carry that field, so a card never
   shows a placeholder. */
function workerFacts(w) {
  const out = [];
  const add = (k, v, mono, title) => {
    if (v === null || v === undefined || v === "") return;
    out.push({ k, v: String(v), mono: Boolean(mono), title: title || "" });
  };
  const u = w.usage;
  const isEstimate = Boolean(w.estimate || (u && u.estimate));

  if (u && (u.input || u.output)) add("Tokens", `${tokens(u.input)} in · ${tokens(u.output)} out${isEstimate ? " (est)" : ""}`, true);
  else if (isEstimate) add("Tokens", "estimate");

  if (w.status === "pending_approval") {
    const qIso = w.created_at;
    if (qIso) add("Queued", fmtClock(qIso), true, qIso);
  } else {
    const startIso = w.started_at || w.created_at;
    if (startIso) add("Started", fmtClock(startIso), true, startIso);

    if (w.completed_at) {
      const st = String(w.status || "").toLowerCase();
      let label = "Finished";
      if (["failed", "budget_stopped", "timed_out"].includes(st)) {
        label = "Failed";
      } else if (["stopped", "rejected"].includes(st)) {
        label = "Stopped";
      } else if (["completed", "success"].includes(st)) {
        label = "Finished";
      }
      let durText = "";
      const startRef = w.created_at || w.started_at;
      if (startRef) {
        const startMs = new Date(startRef).getTime();
        const endMs = new Date(w.completed_at).getTime();
        if (!isNaN(startMs) && !isNaN(endMs) && endMs >= startMs) {
          durText = ` · ${fmtDuration(endMs - startMs)}`;
        }
      }
      if (!durText && w.duration_sec != null) {
        durText = ` · ${fmtDuration(Number(w.duration_sec) * 1000)}`;
      }
      add(label, `${fmtClock(w.completed_at)}${durText}`, true, w.completed_at);
    } else if (["running", "launching", "stopping"].includes(w.status)) {
      const startRef = w.started_at || w.created_at;
      if (startRef) {
        const startMs = new Date(startRef).getTime();
        if (!isNaN(startMs)) {
          const liveMs = Math.max(0, Date.now() - startMs);
          add("Running for", fmtDuration(liveMs), true);
        }
      }
    } else if (w.duration_sec != null) {
      add("Ran for", `${Math.round(Number(w.duration_sec) || 0)}s`, true);
    }
  }

  if (w.exit_code !== null && w.exit_code !== undefined) add("Exit", String(w.exit_code), true);
  if (w.effort) add("Effort", w.effort);
  if (w.verdict === "approve" || w.verdict === "reject") {
    add(w.verdict === "approve" ? "Approved by" : "Rejected by", w.verdict_model || w.model || "reviewer");
  }
  if (w.triggered_by) add("Started by", w.triggered_by, true);
  if (Array.isArray(w.triggered) && w.triggered.length) add("Then", w.triggered.join(", "), true);
  const b = w.budget || {};
  if (b.session_pct) add("Needs 5h", `≥ ${b.session_pct}%`, true);
  if (b.weekly_pct) add("Needs weekly", `≥ ${b.weekly_pct}%`, true);
  if (b.max_usd) add("Spend cap", `$${b.max_usd}`, true);
  if (b.max_tokens) add("Token cap", tokens(b.max_tokens), true);
  if (w.budget_spent && typeof w.budget_spent === "object") {
    for (const [k, v] of Object.entries(w.budget_spent)) add(`${k} spent`, `${v} pts`, true);
  }
  if (!w.skip_permissions && w.permission_mode === "auto_safe") add("Permissions", "Safe commands auto");
  if (w.project_dir) add("Folder", w.project_dir, true);
  return out;
}

/* The handful of states that should be legible without opening the card. */
function workerFlags(w) {
  const flags = [];
  if (w.archived) flags.push(["Archived", "accent", "This worker is archived"]);
  if (w.priority === "urgent") flags.push(["Urgent", "bad", ""]);
  if (w.skip_permissions) flags.push(["Prompts off", "bad", ""]);
  if (w.waiting_permission) flags.push(["Waiting for permission", "accent", ""]);
  if (w.partial_patch) flags.push(["Partial patch kept", "accent", ""]);
  if (w.verify && w.verify.status) {
    const cls = w.verify.status === "PASS" ? "ok" : (w.verify.status === "FLAG" ? "accent" : "bad");
    flags.push([`Verify ${w.verify.status}`, cls, w.verify.line || ""]);
  }
  return flags.map(([label, cls, tip]) => h("span", { class: `wchip ${cls}`.trim(), title: tip || label }, label));
}

function factList(list, cls) {
  if (!list.length) return null;
  return h("dl", { class: `wcard-facts ${cls || ""}`.trim() },
    list.map((f) => [
      h("dt", f.title ? { title: f.title } : {}, f.k),
      h("dd", { class: f.mono ? "mono" : "", ...(f.title ? { title: f.title } : {}) }, f.v),
    ]));
}

let foldSeq = 0;

/* A fixed-height region paged with ‹ › buttons (same pattern and look as the thread card's
   lineage pager), so opening a long card never makes the page itself scroll. The scroll
   position is remembered per key because cards are rebuilt on every refresh. */
const pagedScroll = new Map();
function pagedRegion(key, label, kids) {
  const countEl = h("span", { class: "lineage-head-label" }, "");
  const prevBtn = h("button", { class: "spend-step", type: "button", "aria-label": "Previous page" }, "‹");
  const nextBtn = h("button", { class: "spend-step", type: "button", "aria-label": "Next page" }, "›");
  const content = h("div", { class: "paged-content" }, ...kids);
  const viewport = h("div", { class: "paged-viewport", tabindex: "0", role: "region", "aria-label": label }, content);

  const update = () => {
    const vh = viewport.clientHeight;
    if (!vh) return; /* closed or not yet laid out */
    const maxScroll = Math.max(0, viewport.scrollHeight - vh);
    const total = maxScroll > 2 ? Math.ceil(viewport.scrollHeight / vh) : 1;
    const page = viewport.scrollTop >= maxScroll - 2 ? total : Math.min(total, Math.floor(viewport.scrollTop / vh) + 1);
    countEl.textContent = `${label} · page ${page} of ${total}`;
    prevBtn.disabled = viewport.scrollTop <= 1;
    nextBtn.disabled = maxScroll <= 2 || viewport.scrollTop >= maxScroll - 2;
  };
  const step = (dir) => viewport.scrollBy({ top: dir * (viewport.clientHeight || 320), behavior: "smooth" });
  prevBtn.addEventListener("click", (e) => { e.stopPropagation(); step(-1); });
  nextBtn.addEventListener("click", (e) => { e.stopPropagation(); step(1); });
  let restored = false;
  viewport.addEventListener("scroll", () => {
    /* Before the restore lands, only the user scrolls this fresh viewport, so their scroll wins. */
    restored = true;
    pagedScroll.set(key, viewport.scrollTop);
    update();
  }, { passive: true });
  viewport.addEventListener("keydown", (ev) => {
    if (ev.key === "PageDown" || ev.key === "ArrowDown") { ev.preventDefault(); step(1); }
    else if (ev.key === "PageUp" || ev.key === "ArrowUp") { ev.preventDefault(); step(-1); }
  });

  /* Restore only once the content is tall enough to hold the saved position. Restoring while
     the rebuilt log still read "Loading…" clamped scrollTop to 0, the clamp's scroll event saved
     0 over the real position, and the reader was thrown back to the top on every refresh. */
  const sync = () => {
    if (!restored && viewport.clientHeight > 0) {
      const saved = pagedScroll.get(key) || 0;
      if (saved <= 0) restored = true;
      else if (viewport.scrollHeight - viewport.clientHeight >= saved) {
        viewport.scrollTop = saved;
        restored = true;
      }
    }
    update();
  };
  /* Watch the viewport (opening the card) and the content (a log loading in). */
  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(sync);
    ro.observe(viewport);
    ro.observe(content);
  }
  requestAnimationFrame(sync);

  return h("div", { class: "paged" },
    h("div", { class: "lineage-head paged-head" }, countEl, h("div", { class: "lineage-pager" }, prevBtn, nextBtn)),
    viewport);
}


async function showLog(id) {
  const pre = h("pre", {}, "Loading…");
  modal(`Log: ${id}`, pre);
  try {
    const d = await api(`/api/worker_log?id=${encodeURIComponent(id)}`);
    pre.textContent = d.log || "(no output)";
  } catch (e) {
    pre.textContent = e.message;
  }
}

const _openLogs = new Set();
const logText = new Map(); /* worker id -> last log text, kept across card rebuilds */
export function openLogs(id) {
  return showLog(id);
}
Object.defineProperty(openLogs, "size", { get: () => _openLogs.size });
openLogs.has = (id) => _openLogs.has(id);
openLogs.add = (id) => _openLogs.add(id);
openLogs.delete = (id) => _openLogs.delete(id);
openLogs.clear = () => _openLogs.clear();
export const expandedWorkers = new Set();

function prefersReducedMotion() {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function gridColumnCount(grid) {
  if (!grid) return 1;
  const cards = Array.from(grid.children).filter((el) => {
    if (!el || el.nodeType !== 1) return false;
    if (!el.classList.contains("wcard")) return false;
    const cs = typeof getComputedStyle === "function" ? getComputedStyle(el) : null;
    return !cs || cs.display !== "none";
  });
  if (cards.length < 2) return Math.max(1, cards.length);
  /* Count the grid's column tracks, not cards on the first row: once a card is open it spans
     the row, so a row count always read 1 and the neighbour was pushed down instead of hidden. */
  const tracks = typeof getComputedStyle === "function" ? getComputedStyle(grid).gridTemplateColumns : "";
  const cols = tracks && tracks !== "none" ? tracks.trim().split(/\s+/).length : 1;
  return Math.max(1, cols);
}

function closeCardState(card, id) {
  if (id) expandedWorkers.delete(id);
  card.setAttribute("data-open", "0");
  const toggle = card.querySelector(".wcard-toggle");
  if (toggle) toggle.setAttribute("aria-expanded", "false");
  if (id && openLogs.has(id)) {
    openLogs.delete(id);
    const logPre = card.querySelector(".inline-log");
    const logBtn = card.querySelector("[data-log-toggle]");
    if (logPre) logPre.hidden = true;
    if (logBtn) logBtn.textContent = "Show log";
  }
}

function closeOtherWorkerCards(grid, exceptId) {
  if (!grid) return;
  const cards = Array.from(grid.querySelectorAll(".wcard[data-open=\"1\"]"));
  for (const card of cards) {
    const id = card.getAttribute("data-wid");
    if (!id || id === exceptId) continue;
    /* One open card per row/grid: close others in this grid and clean only their ids. */
    closeCardState(card, id);
  }
  /* Clear side-hidden markers for this grid so sync can recompute cleanly. */
  for (const el of grid.querySelectorAll(".wcard[data-side-hidden-by]")) {
    el.removeAttribute("data-side-hidden-by");
  }
}

function syncSideExpansion(grid) {
  if (!grid) return;
  const side = typeof window !== "undefined" && typeof window.matchMedia === "function"
    && window.matchMedia("(min-width: 768px)").matches;
  const cols = side ? gridColumnCount(grid) : 1;
  for (const el of grid.querySelectorAll(".wcard[data-side-hidden-by]")) {
    el.removeAttribute("data-side-hidden-by");
  }
  if (cols < 2) return;
  const open = grid.querySelector(".wcard[data-open=\"1\"]");
  if (!open) return;
  const visible = Array.from(grid.querySelectorAll(".wcard")).filter((el) => {
    const cs = typeof getComputedStyle === "function" ? getComputedStyle(el) : null;
    return !cs || cs.display !== "none";
  });
  const idx = visible.indexOf(open);
  if (idx < 0) return;
  const inLeft = idx % cols === 0;
  const neighbourIdx = inLeft ? idx + 1 : idx - 1;
  const neighbour = visible[neighbourIdx];
  if (neighbour && neighbour !== open) {
    const by = open.getAttribute("data-wid") || "open";
    neighbour.setAttribute("data-side-hidden-by", by);
  }
}

function runCardOpenTransition(card, apply) {
  const side = typeof window !== "undefined" && typeof window.matchMedia === "function"
    && window.matchMedia("(min-width: 768px)").matches;
  const reduce = prefersReducedMotion();
  if (reduce || !side) { apply(); return; }
  if (typeof document !== "undefined" && typeof document.startViewTransition === "function") {
    try {
      const root = document.documentElement;
      root.classList.add("wcard-vt");
      const vt = document.startViewTransition(() => { apply(); });
      const done = () => root.classList.remove("wcard-vt");
      if (vt && vt.finished && typeof vt.finished.then === "function") vt.finished.then(done, done);
      else done();
      return;
    } catch (_) { /* fall through */ }
  }
  apply();
  /* Graceful fallback when View Transitions unavailable: brief size/opacity hint, honours reduced motion. */
  if (card && typeof card.animate === "function" && !reduce) {
    try {
      card.animate(
        [{ opacity: 0.72, transform: "scale(0.997)" }, { opacity: 1, transform: "none" }],
        { duration: 220, easing: "cubic-bezier(0.23, 1, 0.32, 1)" },
      );
    } catch (_) { /* no-op */ }
  }
}

const observedWorkerGrids = typeof WeakSet !== "undefined" ? new WeakSet() : null;
function observeWorkerGrid(grid) {
  if (!grid || typeof ResizeObserver === "undefined") return;
  if (observedWorkerGrids) {
    if (observedWorkerGrids.has(grid)) return;
    observedWorkerGrids.add(grid);
  } else if (grid.__sideObs) return;
  const ro = new ResizeObserver(() => syncSideExpansion(grid));
  ro.observe(grid);
  if (!observedWorkerGrids) grid.__sideObs = ro;
}

function workerRow(w, past) {
  const tone = WORKER_TONE[w.status] || "";
  const [stateLabel] = WORKER_STATE[w.status] || [w.status || "Unknown", ""];
  const label = (w.status === "pending_approval" && (w.approved || w.approved_by))
    ? "Queued, waiting for a slot"
    : stateLabel;
  const live = ["running", "launching"].includes(w.status);
  const isLogOpen = openLogs.has(w.id);
  if (isLogOpen) expandedWorkers.add(w.id); /* an open log needs an open card */
  const isOpen = expandedWorkers.has(w.id);
  const text = w.prompt || "";
  const provider = String(w.provider || "").toLowerCase();
  /* Boarding-pass header: the route this work took, provider -> model. */
  const modelLabel = (w.model_used && w.model && w.model !== w.model_used)
    ? `${w.model} → ${w.model_used}`
    : (w.model_used || w.model);
  const routeTo = (modelLabel && !["default", "inherit"].includes(modelLabel)) ? modelLabel : "";

  /* Actions: same set and same handlers as before, now in their own row. */
  const actions = [];
  const unarchiveBtn = w.archived ? undoButton({
    label: "Unarchive",
    doneLabel: "Restored",
    icon: "undo",
    duration: 5000,
    onCommit: async () => {
      const res = await act("/api/worker_unarchive", { worker_id: w.id });
      if (res) document.dispatchEvent(new CustomEvent("subway:unarchived", { detail: w.id }));
    },
  }) : null;
  const archiveBtn = !w.archived && !["running", "launching", "stopping", "pending_approval"].includes(w.status) ? undoButton({
    label: "Archive",
    doneLabel: "Archived",
    icon: "archive",
    duration: 5000,
    onCommit: async () => {
      await act("/api/worker_archive", { worker_id: w.id });
    },
  }) : null;
  if (w.status === "pending_approval") {
    if (unarchiveBtn) actions.push(unarchiveBtn);
    if (!w.approved && !w.approved_by) {
      actions.push(h("button", { class: "btn primary", type: "button", on: { click: () => act("/api/approve_worker", { id: w.id }) } }, "Approve"));
    }
    actions.push(h("button", { class: "btn danger", type: "button", on: { click: async () => {
      const reason = await ask("Reject worker", "Reason (optional)");
      if (reason !== null) act("/api/reject_worker", { id: w.id, reason });
    } } }, "Reject"));
  } else {
    if (unarchiveBtn) actions.push(unarchiveBtn);
    if (archiveBtn) actions.push(archiveBtn);
    actions.push(h("button", { class: "btn", type: "button", on: { click: () => act("/api/verify", { id: w.id }) } }, "Verify"));
    /* Report = the worker's final write-up for finished workers. Live/raw output uses "Show log" (inline). */
    if (past) {
      actions.push(h("button", { class: "btn", type: "button", on: { click: () => {
        const hasReport = typeof w.report === "string" ? w.report.trim().length > 0 : Boolean(w.report);
        const repEl = hasReport
          ? h("pre", { class: "report-pre" }, w.report)
          : h("p", { class: "report-empty" }, `No report: this worker ended (${label}) without writing a final message.`);
        const hasReason = typeof w.reason === "string" ? w.reason.trim().length > 0 : Boolean(w.reason);
        const reasonEl = hasReason ? h("p", { class: "report-reason" }, `How it ended: ${w.reason}`) : null;
        modal(`Report: ${w.role || w.id}`, reasonEl ? [repEl, reasonEl] : repEl);
      } } }, "Report"));
    }
    if (w.status === "running") actions.push(h("button", { class: "btn danger", type: "button", on: { click: () => act("/api/stop_worker", { id: w.id, reason: "Stopped from the HUD" }) } }, "Stop"));
  }

  /* Updated in place, so it also works inside the history dialog, which doesn't redraw. */
  /* Start from the last text fetched, so a rebuilt card keeps its height and scroll position. */
  const logPre = h("pre", { class: "inline-log", hidden: !isLogOpen }, logText.get(w.id) || "Loading…");
  const loadLog = () => api(`/api/worker_log?id=${encodeURIComponent(w.id)}`)
    .then((d) => { const t = d.log || "(no output)"; logText.set(w.id, t); if (logPre.textContent !== t) logPre.textContent = t; })
    .catch((e) => { logPre.textContent = e.message; });
  if (isLogOpen) loadLog();

  const logToggleBtn = h("button", {
    class: "btn", type: "button",
    "data-log-toggle": "1",
    on: {
      click: (e) => {
        e.stopPropagation();
        if (openLogs.has(w.id)) openLogs.delete(w.id);
        else openLogs.add(w.id);
        const open = openLogs.has(w.id);
        logPre.hidden = !open;
        e.currentTarget.textContent = open ? "Hide log" : "Show log";
        if (open) {
          /* The log lives in the card's paged drawer, so open the card too, then page to the log. */
          setCardOpen(true);
          loadLog().then(() => {
            const vp = logPre.closest(".paged-viewport");
            if (vp) vp.scrollTop = Math.max(0, logPre.offsetTop - 4); /* reading offsetTop forces layout */
          });
        }
      },
    },
  }, isLogOpen ? "Hide log" : "Show log");
  actions.push(logToggleBtn);

  /* Status pill, polled in place while the worker is live. */
  const pillText = h("span", {}, label);
  const pill = h("span", { class: "wpill" }, live ? h("i", { class: "wpulse" }) : null, pillText);
  if (live) {
    const poll = setInterval(() => {
      if (!pill.isConnected) { clearInterval(poll); return; }
      api(`/api/worker_state?id=${encodeURIComponent(w.id)}`)
        .then((st) => {
          if (st && st.status) {
            const [lbl] = WORKER_STATE[st.status] || [st.status, ""];
            pillText.textContent = lbl;
          }
        })
        .catch(() => { /* keep the last known state */ });
    }, 5000);
  }

  /* Facts: the first four ride on the stub, the rest wait behind the fold. */
  const facts = workerFacts(w);
  const stubFacts = facts.slice(0, 4);
  const foldFacts = facts.slice(4);
  const flags = workerFlags(w);

  const foldId = `wfold-${++foldSeq}`;
  const foldInner = h("div", { class: "wcard-fold-in" }, pagedRegion(`wcard:${w.id}`, "Details", [
    factList(foldFacts),
    text ? h("div", { class: "wcard-brief-heading" }, "Brief") : null,
    text ? h("div", { class: "wcard-body" }, text) : null,
    w.start_note ? h("div", { class: "wcard-note" }, `Start note: ${w.start_note}`) : null,
    w.status_reason ? h("div", { class: "wcard-note" }, `Reason: ${w.status_reason}`) : null,
    w.reason ? h("div", { class: "wcard-note" }, `How it ended: ${w.reason}`) : null,
    (w.verify && w.verify.line) ? h("div", { class: "wcard-note mono" }, w.verify.line) : null,
    w.verdict_reason ? h("div", { class: "wcard-note" }, `Verdict: ${w.verdict_reason}`) : null,
    w.route ? h("div", { class: "wcard-note" }, `Why this agent: ${w.route}`) : null,
    w.progress ? h("div", { class: "wcard-note mono" }, `${w.progress.files_done} of ${w.progress.files_total} files, ${w.progress.chunks_done} chunks done, ${w.progress.failed} failed`) : null,
    (w.status === "pending_approval" && w.approval_code)
      ? h("div", { class: "wcard-note" }, "To approve from a chat, tell the agent this code: ", h("span", { class: "code" }, w.approval_code))
      : null,
    logPre]));
  const fold = h("div", { class: "wcard-fold", id: foldId }, foldInner);

  let card;
  let toggle;
  function setCardOpen(open) {
    const grid = card ? card.closest(".worker-grid") : null;
    if (open && grid) closeOtherWorkerCards(grid, w.id);
    const apply = () => {
      if (open) expandedWorkers.add(w.id);
      else expandedWorkers.delete(w.id);
      card.setAttribute("data-open", open ? "1" : "0");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open && openLogs.has(w.id)) { /* closing the card closes its log too */
        openLogs.delete(w.id);
        logPre.hidden = true;
        logToggleBtn.textContent = "Show log";
      }
      /* Inside apply: a view transition runs this callback later, so syncing
         after runCardOpenTransition would read the card as still closed. */
      if (grid) syncSideExpansion(grid);
    };
    if (grid) observeWorkerGrid(grid);
    runCardOpenTransition(card, apply);
  }
  toggle = h("button", {
    class: "wcard-toggle",
    type: "button",
    "aria-expanded": isOpen ? "true" : "false",
    "aria-controls": foldId,
    "aria-label": "Worker detail",
    on: {
      click: (e) => {
        e.stopPropagation();
        setCardOpen(!expandedWorkers.has(w.id));
      },
    },
  }, icon("caret"));

  card = h("article", {
    class: "wcard",
    "data-wid": w.id,
    "data-tone": tone,
    "data-provider": provider,
    "data-open": isOpen ? "1" : "0",
  },
    h("div", { class: "wcard-head" },
      h("div", { class: "wcard-head-main" },
        h("h3", { class: "wcard-title" }, w.role || "Worker"),
        h("div", { class: "wcard-route" },
          provider ? h("i", { class: "wdot" }) : null,
          provider ? h("b", {}, provider) : null,
          (provider && routeTo) ? h("span", { class: "wcard-arrow" }, "→") : null,
          routeTo ? h("b", { class: "wcard-to" }, routeTo) : null),
        w.id ? h("div", { class: "wcard-id mono" }, w.id) : null),
      h("div", { class: "wcard-head-side" }, pill, toggle)),
    flags.length ? h("div", { class: "wcard-chips" }, flags) : null,
    factList(stubFacts, "wcard-facts-stub"),
    h("div", { class: "wcard-perf", "aria-hidden": "true" }),
    fold,
    actions.length ? h("div", { class: "wcard-acts" }, actions) : null);
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => {
      const g = card.closest(".worker-grid");
      if (g) { observeWorkerGrid(g); syncSideExpansion(g); }
    });
  }
  return card;
}

export function openDelegate(d, prefillPrompt = "") {
  const choices = d.quotas.providers.filter((p) => p.client && p.client.dispatchable);
  const provider = h("select", {}, h("option", { value: "auto" }, "Auto (first with budget)"), choices.map((p) => h("option", { value: p.id }, p.name)));
  const modes = d.settings.permission_modes;
  const permission = h("select", {}, h("option", { value: "" }, `Default: ${modes[d.settings.permission_mode]}`),
    Object.entries(modes).map(([k, v]) => h("option", { value: k }, v)));
  const input = (attrs) => h("input", { autocomplete: "off", ...attrs });
  const role = input({ required: true, maxlength: 80 });
  const model = input({ placeholder: "The agent's default" });
  const effort = h("select", {}, ["auto", "low", "medium", "high", "max"].map((e) => h("option", { value: e }, e[0].toUpperCase() + e.slice(1))));
  const folder = input({ placeholder: "Where the HUD was started" });
  const timeout = input({ type: "number", min: 0, value: 60 });
  const cap5 = input({ type: "number", min: 1, max: 100 });
  const capWk = input({ type: "number", min: 1, max: 100 });
  const usd = input({ type: "number", min: 0, step: 0.5 });
  const maxTokens = input({ type: "number", min: 0 });
  let keepPrivateOn = false;
  const keepPrivate = switchButton({ ariaLabel: "Keep this task on this machine", onChange: (v) => { keepPrivateOn = v; } });
  const prompt = h("textarea", { required: true, value: prefillPrompt });
  let godOn = false;
  const god = switchButton({ ariaLabel: "God mode", onChange: (v) => { godOn = v; } });
  const verdict = h("div", { class: "verdict field full", role: "status" }, "Choose an agent to check its real limits.");
  const num = (el) => (el.value === "" ? null : parseFloat(el.value));

  let timer = 0;
  async function preflight() {
    if (provider.value === "auto") {
      verdict.className = "verdict field full";
      verdict.textContent = "Auto checks each agent's real limits when queued, and picks the first with budget.";
      return;
    }
    try {
      const r = await api("/api/preflight", { provider: provider.value, model: model.value.trim(), budget_session_pct: num(cap5), budget_weekly_pct: num(capWk), max_budget_usd: num(usd) });
      verdict.className = `verdict field full ${r.ok ? "ok" : "bad"}`;
      verdict.textContent = r.result;
    } catch (e) {
      verdict.className = "verdict field full bad";
      verdict.textContent = e.message;
    }
  }
  [provider, model, cap5, capWk, usd].forEach((el) => el.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(preflight, 250); }));

  const form = h("form", { class: "form", on: { submit: async (e) => {
    e.preventDefault();
    const res = await act("/api/spawn", {
      role: role.value.trim(), prompt: prompt.value.trim(), provider: provider.value, model: model.value.trim(), effort: effort.value,
      mode: godOn ? "god" : "standard", permission_mode: permission.value, project_dir: folder.value.trim(),
      budget_session_pct: num(cap5), budget_weekly_pct: num(capWk), max_budget_usd: num(usd),
      max_tokens: num(maxTokens), private: keepPrivateOn,
      timeout_seconds: Math.round((num(timeout) || 0) * 60),
    });
    if (res && /APPROVAL REQUIRED/.test(res.result || "")) dlg.close();
  } } },
    field("Role", role), field("Agent", provider), field("Permissions", permission), field("Model (optional)", model), field("Effort", effort),
    field("Folder (optional)", folder), field("Timeout, minutes (0 for none)", timeout), field("Most of the 5-hour limit to use, points", cap5), field("Most of the weekly limit to use, points", capWk),
    field("Spend cap, USD (Claude or Copilot)", usd), field("Token cap (Antigravity only)", maxTokens), field("Task", prompt, true),
    h("div", { class: "check field full" }, keepPrivate, h("span", {}, "Keep this task on this machine")),
    h("div", { class: "check field full" }, god, h("span", {}, "God mode: maximum effort with permission prompts off. Only for a task you trust completely.")),
    verdict, h("div", { class: "btns field full" }, h("button", { class: "btn primary", type: "submit" }, "Queue for approval")));
  const dlg = modal("Delegate a worker", form);
  preflight();
  role.focus();
}

function openHistory(d, opts = {}) {
  const container = h("div", { class: "rows worker-grid" });
  const loadMoreBtn = h("button", { class: "btn", type: "button", style: "margin-top: 8px; width: 100%; display: none;" }, "Load more");
  const archive = undoButton({
    label: "Archive finished",
    doneLabel: "Archived",
    duration: 5000,
    onCommit: async () => { await act("/api/clear_history", {}); dlg.close(); },
  });

  /* Finished = live records; Archived = archived=only, where each card carries Unarchive. */
  let view = opts && opts.view === "archived" ? "archived" : "finished";
  const segBtn = (id, label) => h("button", {
    type: "button", "aria-pressed": String(view === id),
    on: { click: () => { if (view === id) return; view = id; reset(); } },
  }, label);
  const seg = h("div", { class: "seg history-seg", role: "group", "aria-label": "Which workers to show" },
    segBtn("finished", "Finished"), segBtn("archived", "Archived"));

  const body = h("div", {}, seg, container, loadMoreBtn);
  const dlg = modal("Worker history", body, archive);
  dlg.classList.add("history-dialog");

  let offset = 0;
  const limit = 100;
  let allEntries = [];

  function reset() {
    for (const b of seg.children) b.setAttribute("aria-pressed", String(b.textContent.toLowerCase() === view));
    archive.hidden = view === "archived";
    offset = 0;
    allEntries = [];
    fetchMore();
  }

  /* An unarchived worker leaves the Archived list (it is back in the live records). */
  const onUnarchived = (e) => {
    if (view !== "archived") return;
    allEntries = allEntries.filter((w) => w.id !== e.detail);
    offset = Math.max(0, offset - 1);
    render();
  };
  document.addEventListener("subway:unarchived", onUnarchived);
  dlg.addEventListener("close", () => document.removeEventListener("subway:unarchived", onUnarchived));

  function render() {
    while (container.firstChild) container.removeChild(container.firstChild);
    if (!allEntries.length) {
      container.appendChild(h("div", { class: "empty" }, view === "archived" ? "No archived workers." : "No finished workers."));
    } else {
      for (const w of allEntries) container.appendChild(workerRow(w, true));
    }
  }

  async function fetchMore() {
    loadMoreBtn.disabled = true;
    loadMoreBtn.textContent = "Loading…";
    try {
      const filter = view === "archived" ? "&archived=only" : "";
      const res = await api(`/api/history?offset=${offset}&limit=${limit}${filter}`);
      const entries = res.entries || [];
      const total = res.total != null ? res.total : entries.length;
      allEntries = allEntries.concat(entries);
      offset += entries.length;
      render();

      if (offset < total && entries.length > 0) {
        loadMoreBtn.style.display = "";
        loadMoreBtn.disabled = false;
        loadMoreBtn.textContent = "Load more";
      } else {
        loadMoreBtn.style.display = "none";
      }
    } catch (e) {
      loadMoreBtn.disabled = false;
      loadMoreBtn.textContent = "Error loading history";
    }
  }

  loadMoreBtn.addEventListener("click", fetchMore);
  reset();
}

let modelFilter = "All";
let modelMetric = "overall";
let expandedModelKey = null;
let modelEdits = {};

/* Match a model id against a provider map whose keys may carry a "vendor/" prefix either way. */
function lookupByModel(map, model) {
  if (!map || !model) return undefined;
  if (map[model] !== undefined) return map[model];
  const mLow = model.toLowerCase();
  for (const [k, v] of Object.entries(map)) {
    const kLow = k.toLowerCase();
    if (kLow === mLow || kLow.endsWith("/" + mLow) || mLow.endsWith("/" + kLow)) return v;
  }
  return undefined;
}

const clockTime = (v) => {
  const ts = typeof v === "number" ? (v > 1e11 ? v : v * 1000) : Date.parse(v);
  return ts ? new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
};

export function hasModelEdits() {
  return Object.keys(modelEdits).length > 0;
}

export function modelsPage(d) {
  const settings = d.settings || {};
  const allModels = settings.models || [];
  const bm = settings.benchmarks || {};
  const bmDate = bm.fetched_at ? (bm.fetched_at_iso || new Date(bm.fetched_at * 1000).toLocaleDateString()) : "unknown";
  const bmSource = bm.source || "unknown";
  const bmHost = (() => {
    try {
      const u = new URL(bmSource);
      return u.hostname || bmSource;
    } catch {
      return bmSource;
    }
  })();
  const bmNode = bmSource.startsWith("http://") || bmSource.startsWith("https://")
    ? h("a", { href: bmSource, target: "_blank", rel: "noreferrer", title: bmSource }, bmHost)
    : bmSource;

  const quotaProviders = d.quotas && d.quotas.providers ? d.quotas.providers : [];
  const providerNameById = new Map(quotaProviders.map((p) => [(p.id || "").toLowerCase(), p.name || ""]));
  const providerDisplayName = (id) => {
    const lower = (id || "").toLowerCase();
    if (!lower) return "";
    return providerNameById.get(lower) || lower.charAt(0).toUpperCase() + lower.slice(1);
  };

  const scoreNum = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const benchScores = bm.scores && typeof bm.scores === "object" ? bm.scores : null;
  const keyOf = (m) => `${m.provider}:${m.model || ""}`;
  const skill = (m, k) => {
    const edit = (modelEdits[keyOf(m)] || {})[k];
    return edit !== undefined ? edit : scoreNum(m[k]);
  };

  /* Everything the old per-provider rows showed, gathered once per model so the chart, its
     tooltip and the inline editor all read the same facts. */
  const facts = (m) => {
    const provId = (m.provider || "").toLowerCase();
    const prov = quotaProviders.find((p) => (p.id || "").toLowerCase() === provId || (p.name || "").toLowerCase() === provId);
    const source = m.source === "benchmark" ? "Benchmark" : (m.source === "owner" ? "Yours" : "Default");
    const badges = [];
    const notes = [];
    const ex = (d.exhausted_models || []).find((e) => e.model && m.model && e.model.toLowerCase() === m.model.toLowerCase());
    if (ex && ex.until && clockTime(ex.until)) badges.push({ text: `Out of quota until ${clockTime(ex.until)}`, tone: "bad" });
    let used = null;
    if (prov) {
      const lim = lookupByModel(prov.limited || {}, m.model);
      if (lim && lim.recent) {
        const at = lim.retry_at ? clockTime(lim.retry_at) : "";
        badges.push({ text: at ? `At limit · retries ${at}` : "At limit", tone: "bad" });
      } else {
        notes.push("No limit reported");
      }
      used = Number(lookupByModel(prov.per_model || {}, m.model) || 0);
    }
    if (m.scarce) badges.push({ text: "Scarce", tone: "accent" });
    if (m.free === true) notes.push("Free tier");
    if (m.price_band) notes.push(m.price_band);
    if (m.auto || m.default) notes.push("Auto");
    if (prov && prov.memory) notes.push("Local");

    const tracks = (d.models_track || []).filter((t) => t.model && m.model
      && t.model.toLowerCase() === m.model.toLowerCase()
      && (!t.provider || !m.provider || t.provider.toLowerCase() === m.provider.toLowerCase()));
    let track = null;
    if (tracks.length) {
      const sum = (k) => tracks.reduce((a, x) => a + (x[k] || 0), 0);
      const landed = sum("landed"); const noChange = sum("no_change"); const failed = sum("failed"); const stopped = sum("budget_stopped");
      let rate;
      if (tracks.length === 1 && tracks[0].landed_rate != null) {
        const r = Number(tracks[0].landed_rate);
        rate = r <= 1 && r > 0 ? r * 100 : r;
      } else {
        const total = landed + noChange + failed + stopped;
        rate = total > 0 ? (landed / total) * 100 : 0;
      }
      track = { rate, landed, noChange, failed, stopped, runs: landed + noChange + failed + stopped };
    }
    const c = skill(m, "coding"); const r = skill(m, "reasoning"); const w = skill(m, "writing");
    const direct = benchScores ? scoreNum(benchScores[m.model]) : null;
    const trio = [c, r, w].filter((v) => v != null);
    const overall = direct != null ? direct : (trio.length ? trio.reduce((a, b) => a + b, 0) / trio.length : null);
    return { source, badges, notes, used, track, coding: c, reasoning: r, writing: w, overall };
  };

  const METRICS = {
    overall: { label: "Overall", max: 5, ticks: [0, 1, 2, 3, 4, 5], value: (f) => f.overall, fmt: (v) => `${Math.round(v * 10) / 10}` },
    coding: { label: "Coding", max: 5, ticks: [0, 1, 2, 3, 4, 5], value: (f) => f.coding, fmt: (v) => `${v}` },
    reasoning: { label: "Reasoning", max: 5, ticks: [0, 1, 2, 3, 4, 5], value: (f) => f.reasoning, fmt: (v) => `${v}` },
    writing: { label: "Writing", max: 5, ticks: [0, 1, 2, 3, 4, 5], value: (f) => f.writing, fmt: (v) => `${v}` },
    track: { label: "Track record", max: 100, ticks: [0, 25, 50, 75, 100], value: (f) => (f.track ? f.track.rate : null), fmt: (v) => `${Math.round(v)}%` },
    tokens: { label: "Tokens, 7 days", max: 0, ticks: null, value: (f) => (f.used == null ? null : f.used), fmt: (v) => tokens(v) },
  };
  if (!METRICS[modelMetric]) modelMetric = "overall";
  const metric = METRICS[modelMetric];

  const providers = [];
  for (const m of allModels) {
    const prov = (m.provider || "").toLowerCase();
    if (prov && !providers.includes(prov)) providers.push(prov);
  }
  const rerender = () => document.dispatchEvent(new Event("subway:rerender"));
  const seg = (label, items, current, set) => h("div", { class: "seg seg-compact", role: "group", "aria-label": label },
    items.map(([id, text]) => h("button", {
      type: "button", "aria-pressed": String(String(current).toLowerCase() === String(id).toLowerCase()),
      on: { click: () => { set(id); rerender(); } },
    }, text)));

  const filtered = allModels.filter((m) => !modelFilter || modelFilter.toLowerCase() === "all"
    || (m.provider || "").toLowerCase() === modelFilter.toLowerCase());
  const entries = filtered.map((m) => ({ m, f: facts(m) }))
    .map((x) => ({ ...x, v: metric.value(x.f) }))
    .sort((a, b) => (b.v ?? -Infinity) - (a.v ?? -Infinity) || String(a.m.model).localeCompare(String(b.m.model)));

  const rows = entries.map(({ m, f, v }) => ({
    id: keyOf(m),
    label: m.model || "Default",
    value: v,
    display: v == null ? null : metric.fmt(v),
    color: providerColour(m.provider),
    badges: f.badges,
    m, f,
  }));

  const statLine = (f) => [
    f.track ? `${Math.round(f.track.rate)}% landed (${f.track.landed} of ${f.track.runs})` : "No runs yet",
    f.used != null ? `${tokens(f.used)} tokens, 7 days` : null,
  ].filter(Boolean).join(" · ");

  const tooltip = (row) => {
    const { m, f } = row;
    const pip = (label, val) => h("span", { class: "bc-tip-skill" }, h("span", {}, label), h("b", { class: "num" }, val == null ? "—" : String(val)));
    return h("div", {},
      h("div", { class: "bc-tip-head" },
        h("span", { class: "bc-swatch", style: `background:${row.color}` }),
        h("b", {}, m.model || "Default"),
        h("span", { class: "bc-tip-prov" }, providerDisplayName(m.provider))),
      h("div", { class: "bc-tip-skills" }, pip("Coding", f.coding), pip("Reasoning", f.reasoning), pip("Writing", f.writing)),
      h("div", { class: "bc-tip-line" }, statLine(f)),
      h("div", { class: "bc-tip-line muted" }, [f.source, ...f.notes].join(" · ")));
  };

  const scorePicker = (label, current, onChange) => h("div", { class: "field" },
    h("label", {}, label),
    h("div", { class: "seg seg-compact", role: "group", "aria-label": label },
      [1, 2, 3, 4, 5].map((val) => h("button", {
        type: "button", "aria-pressed": String(current === val),
        on: { click: (e) => { e.stopPropagation(); onChange(val); } },
      }, String(val)))));

  const detail = (row) => {
    const { m, f } = row;
    const key = row.id;
    const update = (k, val) => { modelEdits[key] = { ...(modelEdits[key] || {}), [k]: val }; rerender(); };
    const save = async () => {
      const wholeList = allModels.map((item) => (modelEdits[keyOf(item)] ? { ...item, ...modelEdits[keyOf(item)] } : item));
      const res = await act("/api/models", wholeList);
      if (res) {
        modelEdits = {};
        expandedModelKey = null;
        document.dispatchEvent(new Event("subway:refresh"));
      }
    };
    const dirty = Boolean(modelEdits[key]);
    return h("div", { class: "model-editor" },
      h("div", { class: "model-editor-facts" },
        h("span", { class: "tag" }, providerDisplayName(m.provider)),
        h("span", { class: "tag" }, f.source),
        f.notes.map((n) => h("span", { class: "tag" }, n)),
        h("span", { class: "model-editor-stat" }, statLine(f))),
      h("div", { class: "model-editor-pickers" },
        scorePicker("Coding", f.coding, (v) => update("coding", v)),
        scorePicker("Reasoning", f.reasoning, (v) => update("reasoning", v)),
        scorePicker("Writing", f.writing, (v) => update("writing", v))),
      h("div", { class: "btns" },
        h("button", { class: "btn", type: "button", on: { click: () => { delete modelEdits[key]; expandedModelKey = null; rerender(); } } }, dirty ? "Discard" : "Close"),
        h("button", { class: "btn primary", type: "button", disabled: !dirty, on: { click: save } }, "Save rankings")));
  };

  const scored = entries.filter((x) => x.v != null).length;
  const chartCard = h("section", { class: "card models-card", "aria-labelledby": "models-page-title" },
    h("div", { class: "card-head models-head" },
      h("div", {},
        h("h2", { class: "card-title", id: "models-page-title" }, "Model rankings"),
        h("div", { class: "card-sub" },
          `${filtered.length} model${filtered.length === 1 ? "" : "s"} · ${metric.label}, highest first · `,
          bmNode,
          `, ${bmDate}`))),
    h("div", { class: "models-controls" },
      seg("Measure", Object.entries(METRICS).map(([id, x]) => [id, x.label]), modelMetric, (v) => { modelMetric = v; }),
      seg("Filter by provider", [["All", "All"], ...providers.map((p) => [p, providerDisplayName(p)])], modelFilter, (v) => { modelFilter = v; })),
    modelFilter.toLowerCase() === "all" && providers.length > 1
      ? h("div", { class: "models-legend" }, providers.map((p) => h("span", { class: "models-legend-item" },
        h("span", { class: "bc-swatch", style: `background:${providerColour(p)}` }), providerDisplayName(p))))
      : null,
    barChart({
      id: "models",
      orientation: "vertical",
      rows,
      max: metric.max,
      ticks: metric.ticks,
      format: metric.fmt,
      animateKey: `${modelMetric}|${modelFilter}|${rows.map((r) => r.id).join(",")}`,
      selectedId: expandedModelKey,
      onSelect: (id) => { expandedModelKey = id; rerender(); },
      detail,
      tooltip,
      emptyText: "No models match this filter.",
      ariaLabel: `Models by ${metric.label}`,
    }),
    scored < entries.length
      ? h("div", { class: "card-sub models-foot" }, `${entries.length - scored} without a ${metric.label.toLowerCase()} value are listed last.`)
      : null);

  const howCard = h("section", { class: "card", "aria-labelledby": "how-title" },
    h("div", { class: "card-head" },
      h("h2", { class: "card-title", id: "how-title" }, "How this works")),
    h("div", { class: "card-sub" },
      "Routing picks a model using the task's difficulty, the role's skill, then remaining allowance. Benchmark means LMArena, Default means our starting estimate, Yours means you edited it. Rankings measure strength, not allowance. Select a model to change its rankings."));

  return [chartCard, howCard];
}

let archivedCount = null; // last archived-worker total, kept across workersPage rebuilds
export function workersPage(d) {
  const workers = d.workers || [];
  const activeCount = workers.filter((w) => ["running", "launching", "stopping"].includes(w.status)).length;
  const cap = (d.settings && d.settings.capacity) || {};
  const limits = (!d.settings || !d.settings.capacity || cap.limits_off)
    ? " No worker limits (your setting)."
    : ((cap.chat != null && cap.machine != null)
      ? ` Limits: ${cap.chat} per chat, ${cap.machine} on this machine.`
      : (cap.chat != null ? ` Limits: ${cap.chat} per chat.` : (cap.machine != null ? ` Limits: ${cap.machine} on this machine.` : "")));
  const sub = `${activeCount} running.${limits}`;

  const budgetVal = (d.settings && d.settings.chat_budget != null) ? d.settings.chat_budget : "";
  const budgetInput = h("input", { type: "number", min: 0, style: "width: 140px;", value: budgetVal });
  const budgetSaveBtn = h("button", {
    class: "btn primary",
    type: "button",
    on: {
      click: () => {
        const n = parseInt(budgetInput.value, 10);
        return act("/api/chat_budget", { tokens: Number.isFinite(n) ? n : 0 });
      },
    },
  }, "Save");

  const chatRows = (d.settings && d.settings.chat_budgets && d.settings.chat_budgets.chats) || [];
  const chatRowEls = chatRows.map((c) => {
    const tag = c.status === "warning" ? h("span", { class: "tag accent" }, "Almost gone")
      : c.status === "reached" ? h("span", { class: "tag bad" }, "Reached, work stopped") : null;
    const clearBtn = h("button", {
      class: "btn",
      type: "button",
      on: {
        click: async () => {
          await act("/api/chat_budget", { tokens: 0, session_id: c.session_id });
        },
      },
    }, "Clear");
    return h("div", { class: "row budget-row" },
      h("div", { class: "budget-main" },
        h("div", { class: "budget-head" }, h("span", { class: "row-title" }, c.label || c.session_id), tag),
        h("div", { class: "row-sub num" }, `${tokens(c.used)} of ${tokens(c.limit)} tokens`)),
      clearBtn);
  });

  const chatBudgetCard = h("section", { class: "card" },
    h("div", { class: "card-head" },
      h("div", {},
        h("h2", { class: "card-title" }, "Universal chat budget"),
        h("div", { class: "card-sub" }, "Default token limit for chats with no budget of their own")),
      h("div", { style: "display: flex; gap: 8px; align-items: center; flex-wrap: wrap;" },
        budgetInput,
        budgetSaveBtn)),
    chatRowEls.length ? h("div", { class: "rows" },
      h("div", { class: "card-sub" }, "Chats with their own budget:"),
      chatRowEls) : null);

  const budgets = d.settings && d.settings.budgets;
  const hasChatScope = Array.isArray(budgets) && budgets.some((b) => b.scope === "chat");

  const running = workers.filter((w) => ["running", "launching", "stopping"].includes(w.status));
  const waiting = workers.filter((w) => w.status === "pending_approval");
  const finished = workers.filter((w) => !["running", "launching", "stopping", "pending_approval"].includes(w.status));
  const totalHist = d.total_history != null ? d.total_history : (d.history || []).length;
  const stat = (label, value, cls) => h("div", { class: `worker-stat ${cls || ""}`.trim() },
    h("div", { class: "worker-stat-v num" }, String(value)), h("div", { class: "worker-stat-k" }, label));
  // The page is rebuilt on every refresh. Start from the last known count so the label (and
  // with it the header's wrap point) doesn't flip between "Archived" and "Archived (N)" each poll.
  const archivedLabel = () => (archivedCount == null ? "Archived" : `Archived (${archivedCount})`);
  const archivedBtn = h("button", { class: "btn", type: "button", on: { click: () => openHistory(get("status"), { view: "archived" }) } }, archivedLabel());
  api("/api/history?archived=only&limit=0").then((res) => {
    const n = res && res.total != null ? res.total : ((res && res.entries) ? res.entries.length : 0);
    if (n !== archivedCount) { archivedCount = n; archivedBtn.textContent = archivedLabel(); }
  }).catch(() => {});
  const hero = h("section", { class: "card worker-hero" },
    h("div", { class: "worker-hero-top" },
      h("div", {}, h("h2", { class: "card-title" }, "Workers"), h("div", { class: "card-sub" }, sub)),
      h("div", { class: "btns" },
        h("button", { class: "btn", type: "button", on: { click: () => openHistory(get("status")) } }, `History (${totalHist})`),
        archivedBtn,
        h("button", { class: "btn primary", type: "button", on: { click: () => openDelegate(get("status")) } }, "Delegate worker"))),
    h("div", { class: "worker-stats-row" },
      stat("Running", running.length, running.length ? "live" : ""),
      stat("Waiting on you", waiting.length, waiting.length ? "warn" : ""),
      stat("Pending approvals", (d.permissions || []).length),
      stat("Finished", totalHist)));
  const group = (title, list) => list.length ? h("section", { class: "card worker-group" },
    h("div", { class: "card-head" }, h("h2", { class: "card-title" }, title), h("span", { class: "tag num" }, String(list.length))),
    h("div", { class: "rows worker-grid" }, list.map((w) => workerRow(w, false)))) : null;

  return [
    permissionsCard(d.permissions || [], d.settings || {}),
    h("div", { class: "controls-grid" }, settingsCard(d.settings, (d.permissions || []).length), terminalCommandsCard(d.settings)),
    hasChatScope ? null : chatBudgetCard,
    hero,
    group("Running", running),
    group("Waiting on you", waiting),
    group("Finished", finished),
    !workers.length ? h("section", { class: "card" }, h("div", { class: "empty" }, "No active workers. Delegate one to get started, or open History for finished ones.")) : null,
  ].filter(Boolean);
}

/* ---- Leases ---- */
function openClaim() {
  const path = h("input", { required: true, autocomplete: "off" });
  const holder = h("input", { autocomplete: "off", value: "hud-user" });
  const secs = h("input", { type: "number", min: 10, max: 3600, value: 300 });
  const role = h("select", {}, ["implementer", "reviewer", "refactorer", "coordinator"].map((r) => h("option", { value: r }, r)));
  const form = h("form", { class: "form", on: { submit: async (e) => {
    e.preventDefault();
    const res = await act("/api/claim", { filepath: path.value.trim(), holder: holder.value.trim(), role: role.value, duration: parseInt(secs.value, 10) || 300 });
    if (res && /LEASE ACQUIRED/.test(res.result || "")) dlg.close();
  } } }, field("Absolute file path", path, true), field("Holder", holder), field("Seconds", secs), field("Role", role),
    h("div", { class: "btns field full" }, h("button", { class: "btn primary", type: "submit" }, "Claim")));
  const dlg = modal("Claim a file", form);
  path.focus();
}

export function leasesPage(d) {
  const rows = [];
  for (const [path, l] of Object.entries(d.locks || {})) {
    const isAutoClaimed = Boolean(l.purpose && l.purpose.includes("auto-claimed"));
    rows.push(h("div", { class: "row" },
      h("div", {}, h("div", { class: "row-title mono" }, path), h("div", { class: "row-sub" }, l.purpose || "")),
      h("div", { class: "tags" },
        isAutoClaimed ? h("span", { class: "tag" }, "auto-claimed") : null,
        h("span", { class: "tag accent" }, l.holder || "unknown"), h("span", { class: "tag" }, l.role || "implementer"),
        h("span", { class: "tag num" }, ticking("span", {}, l.expires_at, "countdown"), " left")),
      h("div", { class: "btns" }, h("button", { class: "btn danger", type: "button", on: { click: () => act("/api/release", { filepath: path }) } }, "Release"))));
  }
  for (const [path, so] of Object.entries(d.signoffs || {})) {
    const ok = so.verdict === "APPROVED";
    rows.push(h("div", { class: "row" },
      h("div", {}, h("div", { class: "row-title mono" }, path), h("div", { class: "row-sub" }, so.notes || "")),
      h("div", { class: "tags" }, h("span", { class: `tag ${ok ? "ok" : ""}` }, ok ? "Approved" : "Needs work"), h("span", { class: "tag" }, so.reviewer || ""),
        so.commit_sha ? h("span", { class: "tag mono" }, so.commit_sha) : null, so.drifted ? h("span", { class: "tag bad" }, "Changed since sign-off") : null)));
  }
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", { class: "card-title" }, "File leases"),
      h("div", { class: "card-sub" }, "Enforced for edits in Claude Code once its hook is installed. Advisory for other agents.")),
      h("button", { class: "btn", type: "button", on: { click: openClaim } }, "Claim file")),
    h("div", { class: "rows" }, rows.length ? rows : h("div", { class: "empty" }, "No leases or sign-offs.")));
}

/* Threads (roadmap 7/41): lifecycle, activities, image and canvas views, and management. */

export const THREAD_STATES = {
  working: { label: "Working", cls: "working" },
  monitoring: { label: "Monitoring", cls: "monitoring" },
  done: { label: "Done", cls: "done" },
  snoozed: { label: "Snoozed", cls: "snoozed" },
  settled: { label: "Settled", cls: "settled" },
};

/* Subway's own verbs sit beside the T3-style lifecycle: what a thread is doing right now. */
export const ACTIVITIES = {
  spinning: "Spinning up",
  delegating: "Delegating",
  planning: "Planning",
  building: "Building",
  reviewing: "Reviewing",
  mailing: "Mailing",
  posting: "Posting",
  handing_off: "Handing off",
  rerouting: "Rerouting",
  escalating: "Escalating",
  waiting: "Waiting on you",
  terminal: "Terminal run",
  idle: "Idle",
};

export const threads = { chats: [], loadedAt: 0, loading: false, error: "", scope: "subway", view: "image", graphSel: "", meta: {} };

/* Which hidden panels are open, keyed by thread + panel, so a re-render never collapses them. */
const openToggles = new Set();

function provKey(chat) {
  const src = String((chat && (chat.source || chat.provider)) || "").trim().toLowerCase();
  return src || "custom";
}

function workerTokens(w) {
  if (!w) return 0;
  if (w.tokens != null) return Number(w.tokens) || 0;
  const u = w.usage || {};
  const t = u.total != null ? Number(u.total) : (Number(u.input) || 0) + (Number(u.output) || 0);
  return t || 0;
}

function threadTotals(chat) {
  const workers = Array.isArray(chat.workers) ? chat.workers : [];
  const workerT = workers.reduce((a, w) => a + workerTokens(w), 0);
  const initiatorT = chat.tokens != null ? Number(chat.tokens) || 0 : ((chat.usage && Number(chat.usage.total)) || 0);
  return { initiatorT, totalT: initiatorT + workerT, nodes: workers.length + 1 };
}

function lifecycleOf(chat) {
  if (chat.lifecycle && THREAD_STATES[chat.lifecycle]) return chat.lifecycle;
  const workers = Array.isArray(chat.workers) ? chat.workers : [];
  if (workers.some((w) => ["launching", "running", "pending_approval", "stopping"].includes(w.status))) return "working";
  if (workers.length) return "monitoring";
  return chat.open ? "monitoring" : "done";
}

function activityOf(chat) {
  if (chat.activity && ACTIVITIES[chat.activity]) return chat.activity;
  const workers = Array.isArray(chat.workers) ? chat.workers : [];
  const live = workers.find((w) => ["launching", "running", "pending_approval"].includes(w.status));
  if (!live) return "idle";
  if (live.status === "pending_approval") return "waiting";
  const role = String(live.role || "").toLowerCase();
  if (role.includes("plan") || role.includes("architect") || role.includes("overseer")) return "planning";
  if (role.includes("review") || role.includes("verif")) return "reviewing";
  return "building";
}

function statePill(key) {
  const st = THREAD_STATES[key] || THREAD_STATES.done;
  return h("span", { class: `state-pill ${st.cls}` }, h("span", { class: "sd" }), st.label);
}

function activityChip(key) {
  if (!key || key === "idle") return null;
  return h("span", { class: `act ${key}` }, ACTIVITIES[key] || key);
}

function gauge(k, v, extra, barPct) {
  const val = h("div", { class: "v" }, v, extra ? h("small", {}, ` ${extra}`) : null);
  if (barPct != null) val.append(h("div", { class: "bar", style: "margin-top:6px" }, h("div", { class: `bar-fill${barPct >= 90 ? " bad" : ""}`, style: `width:${barPct}%` })));
  return h("div", { class: "gauge" }, h("div", { class: "k" }, k), val);
}

function hiddenToggle(label, panel, count, key) {
  const id = key || label;
  const isOpen = openToggles.has(id);
  const arrow = h("span", { "aria-hidden": "true" }, isOpen ? " \u25b4" : " \u25be");
  const btn = h("button", { class: "hide-toggle", type: "button", "aria-expanded": String(isOpen) }, `${label}${count ? ` (${count})` : ""}`, arrow);
  panel.dataset.hidden = String(!isOpen);
  btn.addEventListener("click", () => {
    const open = btn.getAttribute("aria-expanded") === "true";
    if (open) openToggles.delete(id); else openToggles.add(id);
    btn.setAttribute("aria-expanded", String(!open));
    arrow.textContent = open ? " \u25be" : " \u25b4";
    panel.dataset.hidden = open ? "true" : "false";
  });
  return h("div", { style: "display:flex;flex-direction:column;gap:10px" }, btn, panel);
}

/* Per-thread settings as a drill-down stack panel, matching the main
   Settings revamp. One panel per chat, rebuilt in place on each render. */
const threadPanels = new Map();

function threadSettings(chat, d) {
  const st = (d && d.settings) || {};
  const modes = st.permission_modes || { ask: "Ask every time", auto_safe: "Auto-approve safe tools", auto_approve: "Auto-approve everything" };
  const universal = {
    permission_mode: st.permission_mode || "ask",
    terminal_mode: st.terminal_mode || "ask",
    budget_tokens: st.chat_budget != null ? st.chat_budget : null,
    compaction: st.compaction || "suggest",
  };
  const ov = chat.thread_settings || {};
  const pm = ov.permission_mode;
  const tm = ov.terminal_mode;
  const bt = ov.budget_tokens;
  const cp = ov.compaction;
  const post = (body) => act("/api/thread_settings", { id: chat.id, ...body });

  const modeLabel = (v) => modes[v] || v;
  const compactionLabel = { suggest: "Suggest", auto: "Auto", never: "Never" };
  const choice = (id, label, selected, onSelect) => ({ id, label, selected: Boolean(selected), onSelect });

  const budgetStepper = (value, placeholder, onChange) => {
    const current = () => (Number(value) || Number(placeholder) || 0);
    return h("span", { class: "sp-stepper" },
      h("button", { type: "button", class: "sp-step", "aria-label": "Decrease", on: { click: () => onChange(Math.max(0, current() - 25000)) } }, "\u2212"),
      h("input", {
        type: "number", class: "sp-num", min: 0, inputmode: "numeric",
        "aria-label": "Token budget", value: value ?? "", placeholder: String(placeholder ?? 0),
        on: { change: (e) => { const v = parseInt(e.target.value, 10); onChange(Number.isFinite(v) && v > 0 ? v : ""); } },
      }),
      h("button", { type: "button", class: "sp-step", "aria-label": "Increase", on: { click: () => onChange(current() + 25000) } }, "+"));
  };

  const pages = {
    settings: {
      title: "Settings",
      sections: [{
        rows: [
          { id: "pm", label: "Worker permission", detail: pm != null ? modeLabel(pm) : `Inherit \u00b7 ${modeLabel(universal.permission_mode)}`, to: "pm" },
          { id: "tm", label: "Terminal command", detail: tm != null ? modeLabel(tm) : `Inherit \u00b7 ${modeLabel(universal.terminal_mode)}`, to: "tm" },
          { id: "bt", label: "Token budget", detail: bt != null ? tokens(bt) : `Inherit \u00b7 ${universal.budget_tokens ? tokens(universal.budget_tokens) : "none"}`, to: "bt" },
          { id: "cp", label: "Compaction", detail: cp != null ? (compactionLabel[cp] || cp) : `Inherit \u00b7 ${compactionLabel[universal.compaction] || universal.compaction}`, to: "cp" },
        ],
      }],
    },
    pm: {
      title: "Worker permission",
      sections: [{
        title: `General: ${modeLabel(universal.permission_mode)}`,
        rows: [
          choice("inherit", "Inherit general", pm == null, () => post({ permission_mode: "" })),
          ...Object.entries(modes).map(([k, lbl]) => choice(k, lbl, pm === k, () => post({ permission_mode: k }))),
        ],
        footer: "Applies to this chat and the workers it spins.",
      }],
    },
    tm: {
      title: "Terminal command",
      sections: [{
        title: `General: ${modeLabel(universal.terminal_mode)}`,
        rows: [
          choice("inherit", "Inherit general", tm == null, () => post({ terminal_mode: "" })),
          ...Object.entries(modes).map(([k, lbl]) => choice(k, lbl, tm === k, () => post({ terminal_mode: k }))),
        ],
        footer: "Protected commands always ask, whatever this says.",
      }],
    },
    bt: {
      title: "Token budget",
      sections: [
        {
          rows: [{ id: "budget", label: "Budget for this chat", trailing: budgetStepper(bt, universal.budget_tokens || 0, (v) => post({ budget_tokens: v })) }],
          footer: bt != null ? "0 clears the override and follows the general budget." : `General budget: ${universal.budget_tokens ? tokens(universal.budget_tokens) : "none"}.`,
        },
        {
          rows: [choice("general", "Follow general", bt == null, () => post({ budget_tokens: "" }))],
        },
      ],
    },
    cp: {
      title: "Compaction",
      sections: [{
        title: `General: ${compactionLabel[universal.compaction] || universal.compaction}`,
        rows: [
          choice("inherit", "Inherit general", cp == null, () => post({ compaction: "" })),
          choice("suggest", "Suggest", cp === "suggest", () => post({ compaction: "suggest" })),
          choice("auto", "Auto", cp === "auto", () => post({ compaction: "auto" })),
          choice("never", "Never", cp === "never", () => post({ compaction: "never" })),
        ],
        footer: "What happens when this chat nears its context window.",
      }],
    },
  };

  /* Reuse by id, not by isConnected: the old card tree is already detached by
     the time this runs during a re-render, and a fresh panel would forget the
     page the reader had drilled into. */
  const cached = threadPanels.get(chat.id);
  if (cached) {
    cached.__sp.update(pages);
    return cached;
  }
  const panel = stackPanel({ pages, root: "settings" });
  panel.classList.add("thread-sp");
  threadPanels.set(chat.id, panel);
  return panel;
}

const lineageScroll = new Map();

function lineageTree(chat) {
  const workers = Array.isArray(chat.workers) ? chat.workers : [];
  if (!workers.length) return h("div", { class: "row-sub" }, "No workers yet.");
  const depth = {};
  for (const w of workers) {
    const p = w.parent_id || w.triggered_by;
    depth[w.id] = (p && depth[p] != null) ? depth[p] + 1 : (p ? 1 : 0);
  }

  const chatId = chat.id || "default";
  const savedScroll = lineageScroll.get(chatId) || 0;

  const countEl = h("span", { class: "lineage-head-label" }, "");
  const prevBtn = h("button", { class: "spend-step", type: "button", "aria-label": "Previous page" }, "\u2039");
  const nextBtn = h("button", { class: "spend-step", type: "button", "aria-label": "Next page" }, "\u203a");
  const head = h("div", { class: "lineage-head" },
    countEl,
    h("div", { class: "lineage-pager" }, prevBtn, nextBtn));

  const rows = workers.map((w) => {
    const dep = w.depth != null ? w.depth : (depth[w.id] || 0);
    const t = workerTokens(w);
    const v = String(w.verdict || "").toLowerCase();
    const ended = ["completed", "failed", "stopped", "timed_out", "budget_stopped", "rejected"].includes(w.status);
    const dur = w.duration_sec != null ? `${w.duration_sec}s` : (ended ? "" : "running");
    const statusCls = w.status === "completed" ? "tag ok" : (["failed", "stopped", "timed_out", "budget_stopped", "rejected"].includes(w.status) ? "tag bad" : "tag");
    const verdictCls = (v === "approve" || v === "approved") ? "tag bare ok" : ((v === "reject" || v === "rejected") ? "tag bare bad" : "tag bare");

    return h("div", { class: "lineage-node", style: dep > 0 ? `margin-left:${dep * 14}px` : "" },
      h("div", { class: "lineage-node-main" },
        h("div", { class: "lineage-role" }, w.role || "worker"),
        h("div", { class: "mono dim" }, [w.provider, w.model].filter(Boolean).join(" \u00b7 "))),
      h("div", { class: "lineage-node-meta tags" },
        h("span", { class: `${statusCls} bare` }, w.status || "unknown"),
        v ? h("span", { class: verdictCls }, v === "approve" ? "APPROVE" : (v === "reject" ? "REJECT" : v.toUpperCase())) : null,
        t ? h("span", { class: "tag num bare" }, `${tokens(t)} tokens`) : null,
        dur ? h("span", { class: "tag num bare" }, dur) : null,
        h("button", { class: "btn", type: "button", on: { click: () => openLogs(w.id) } }, "Logs")));
  });

  const viewport = h("div", { class: "lineage-viewport", tabindex: "0", "role": "region", "aria-label": "Lineage list" }, ...rows);

  function updatePager() {
    const h = viewport.clientHeight || 320;
    const maxScroll = Math.max(0, viewport.scrollHeight - h);
    let totalPages = 1;
    let page = 1;
    if (maxScroll > 2 && h > 0) {
      totalPages = workers.length ? Math.max(1, Math.ceil((viewport.scrollHeight - 2) / h)) : 1;
      if (viewport.scrollTop >= maxScroll - 2) {
        page = totalPages;
      } else {
        page = Math.min(totalPages, Math.max(1, Math.floor(viewport.scrollTop / h) + 1));
      }
    }
    countEl.textContent = `${workers.length} ${workers.length === 1 ? "worker" : "workers"} \u00b7 page ${page} of ${totalPages}`;
    prevBtn.disabled = viewport.scrollTop <= 1;
    nextBtn.disabled = maxScroll <= 2 || viewport.scrollTop >= maxScroll - 2;
  }

  const scrollPage = (dir) => {
    const h = viewport.clientHeight || 320;
    viewport.scrollBy({ top: dir * h, behavior: "smooth" });
  };

  prevBtn.addEventListener("click", () => scrollPage(-1));
  nextBtn.addEventListener("click", () => scrollPage(1));

  viewport.addEventListener("scroll", () => {
    lineageScroll.set(chatId, viewport.scrollTop);
    updatePager();
  }, { passive: true });

  viewport.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "PageDown") {
      ev.preventDefault();
      scrollPage(1);
    } else if (ev.key === "ArrowUp" || ev.key === "PageUp") {
      ev.preventDefault();
      scrollPage(-1);
    }
  });

  const restore = () => {
    if (viewport.clientHeight > 0) {
      if (savedScroll > 0) viewport.scrollTop = savedScroll;
      updatePager();
    }
  };

  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => restore());
    ro.observe(viewport);
  }
  requestAnimationFrame(restore);
  updatePager();

  return h("div", { class: "lineage-tree" }, head, viewport);
}

function trainEmpty() {
  return h("div", { class: "train-empty" }, trainMark(),
    h("b", {}, "No threads yet"),
    h("p", {}, "Spin a worker from any chat, or dispatch one from this HUD, and its whole story \u2014 nodes, edges, cost and settings \u2014 appears here."));
}

function threadCard(chat, d, onRefresh) {
  const life = lifecycleOf(chat);
  const act = activityOf(chat);
  const { totalT, nodes } = threadTotals(chat);
  const workers = Array.isArray(chat.workers) ? chat.workers : [];
  const meta = chat.meta || {};
  const updated = chat.updated ? new Date(typeof chat.updated === "number" ? chat.updated * 1000 : chat.updated).toLocaleString() : "";

  const kebab = h("button", { class: "icon-btn", type: "button", "aria-label": "Thread actions",
    on: { click: (ev) => { ev.stopPropagation(); openThreadMenu(ev, chat, onRefresh); } } }, icon("dots"));

  const head = h("div", { class: "thread-card-head" },
    h("div", { class: "thread-title-area" },
      h("div", { class: "thread-chips" }, statePill(life), activityChip(act), meta.pinned ? h("span", { class: "tag accent" }, "pinned") : null),
      h("h3", { class: "thread-title" }, chat.title || "(untitled)"),
      h("div", { class: "thread-crumbs" }, [chat.source, chat.model, chat.project].filter(Boolean).join(" \u00b7 ") || "\u2014")),
    kebab);

  const ctx = (d && d.chat && d.chat.id === chat.id && d.chat.context) ? d.chat.context : null;
  const pctNum = ctx ? Math.min(100, Math.max(0, Number(ctx.pct) || 0)) : null;
  const gauges = h("div", { class: "thread-gauges" },
    gauge("Initiator", chat.tokens != null ? tokens(chat.tokens) : "\u2014"),
    gauge("Graph total", totalT > 0 ? tokens(totalT) : "\u2014", chat.cost_usd != null ? `\u00b7 $${Number(chat.cost_usd).toFixed(2)}` : ""),
    gauge("Nodes", String(nodes)),
    gauge("Context", pctNum != null ? `${pctNum}%` : "\u2014", "", pctNum));

  const foot = h("div", { class: "thread-chips" },
    h("button", { class: "btn", type: "button", on: { click: () => openCanvas(chat.id) } }, "Canvas"),
    updated ? h("span", { class: "row-sub" }, `Updated ${updated}`) : null,
    h("span", { class: "row-sub" }, `requested by ${chat.requested_by || "unknown"} \u00b7 paid by ${chat.paid_by || "unknown"}`));

  return h("div", { class: "thread-card", "data-thread": chat.id,
    on: { contextmenu: (ev) => { ev.preventDefault(); openThreadMenu(ev, chat, onRefresh); } } },
    head, gauges,
    hiddenToggle("Settings", threadSettings(chat, d), 4, `${chat.id}:settings`),
    hiddenToggle("Lineage", lineageTree(chat), workers.length, `${chat.id}:lineage`),
    foot);
}

function openCanvas(id) {
  threads.view = "canvas";
  threads.graphSel = id;
  document.dispatchEvent(new Event("subway:rerender"));
}

function graphCanvas(chat, readout) {
  const workers = Array.isArray(chat.workers) ? chat.workers : [];
  const rootId = chat.id || "thread";
  const nodes = [{
    id: rootId, label: chat.title || "Initiator", sub: chat.model || "", status: "completed", depth: 0,
  }].concat(workers.map((w) => ({
    id: w.id, label: w.role || "worker", sub: [w.provider, w.model].filter(Boolean).join(" \u00b7 "),
    status: w.status, verdict: w.verdict, tokens: workerTokens(w),
  })));
  const byId = new Map(nodes.map((n) => [n.id, n]));

  /* Parent of each worker: its declared parent when known, else the root. */
  const parentOf = new Map();
  workers.forEach((w) => {
    const p = w.parent_id || w.triggered_by;
    parentOf.set(w.id, p && byId.has(p) && p !== w.id ? p : rootId);
  });

  /* Depth per node from the parent chain, guarding against cycles (max 64 hops). */
  const depth = new Map([[rootId, 0]]);
  const depthOf = (id, hops) => {
    if (depth.has(id)) return depth.get(id);
    if (hops > 64) return 1;
    const d = depthOf(parentOf.get(id) || rootId, hops + 1) + 1;
    depth.set(id, d);
    return d;
  };
  nodes.forEach((n) => { n.depth = depthOf(n.id, 0); });

  const links = workers.map((w) => ({ source: parentOf.get(w.id), target: w.id }));

  /* One lane colour per depth-1 branch, like a git graph. */
  const branchOf = (id) => {
    let cur = id, guard = 0;
    while ((depth.get(cur) || 0) > 1 && guard++ < 64) {
      const p = parentOf.get(cur);
      if (!p || !byId.has(p)) break;
      cur = p;
    }
    return cur;
  };
  const laneIndex = new Map();
  nodes.forEach((n) => {
    const lane = branchOf(n.id);
    if (!laneIndex.has(lane)) laneIndex.set(lane, laneIndex.size);
  });
  const laneColour = (id) => `hsl(${((laneIndex.get(branchOf(id)) || 0) * 137.508) % 360} 66% 60%)`;

  return sankeyChart({
    key: chat.id,
    nodes,
    links,
    colour: (n) => laneColour(n.id),
    readout,
    onClick: (id) => { const n = nodes.find((x) => x.id === id); if (n) openNode(n, chat); },
  });
}

function openNode(n, chat) {
  const sub = n.sub || [n.provider, n.model].filter(Boolean).join(" \u00b7 ");
  const live = isWorkerId(n.id) && ["running", "launching"].includes(n.status);
  // A running worker can be steered from the composer docked under this canvas.
  const steer = live ? h("button", { class: "btn primary", type: "button", on: { click: () => {
    dlg.close();
    steerFromComposer({ kind: "worker", id: n.id, label: n.label || n.role || n.id, sub });
  } } }, "Steer in composer") : null;
  const dlg = modal(n.label || n.role || "Node", h("div", { class: "rows" },
    h("div", { class: "thread-crumbs" }, [sub, `status ${n.status || "\u2014"}`].filter(Boolean).join(" \u00b7 ")),
    n.tokens ? h("div", { class: "row-sub" }, `${tokens(n.tokens)} tokens`) : null,
    n.verdict ? h("div", { class: "row-sub" }, `verdict ${String(n.verdict).toUpperCase()}`) : null,
    h("div", { class: "btns" }, steer,
      (n.id && n.id !== chat.id) ? h("button", { class: "btn", type: "button", on: { click: () => openLogs(n.id) } }, "Open log") : null)));
}

function threadCanvasView(chats) {
  if (!chats.some((c) => c.id === threads.graphSel)) threads.graphSel = chats[0].id;
  const shown = chats.slice(0, 14);
  const picker = h("div", { class: "thread-picker" }, shown.map((c) => {
    return h("button", { class: "thread-pick", type: "button", "aria-pressed": String(c.id === threads.graphSel),
      on: { click: () => { threads.graphSel = c.id; document.dispatchEvent(new Event("subway:rerender")); } } },
      c.title || "(untitled)");
  }), chats.length > shown.length ? h("span", { class: "row-sub", style: "align-self:center;white-space:nowrap" }, `+${chats.length - shown.length} more`) : null);

  const readout = h("div", { class: "graph-readout", hidden: true });
  const chat = chats.find((c) => c.id === threads.graphSel) || chats[0];
  const canvas = graphCanvas(chat, readout);
  const zoomLabel = h("span", { class: "gt-pct" }, `${Math.round((canvas._zoom || 1) * 100)}%`);
  canvas.addEventListener("zoomchange", (e) => { zoomLabel.textContent = `${Math.round(e.detail * 100)}%`; });
  const zbtn = (label, fn, title) => h("button", { class: "gt-btn", type: "button", title, "aria-label": title, on: { click: fn } }, label);
  const toolbar = h("div", { class: "graph-toolbar" },
    h("span", { class: "row-sub" }, `${canvas._lanes ? canvas._lanes + " lanes \u00b7 " : ""}${canvas._nodes} node${canvas._nodes === 1 ? "" : "s"} \u00b7 hover a node to trace it; drag to pan, \u2318/pinch to zoom`),
    h("div", { class: "gt-controls" },
      zbtn("\u2212", () => canvas._setZoom(canvas._zoom / 1.25), "Zoom out"),
      zoomLabel,
      zbtn("+", () => canvas._setZoom(canvas._zoom * 1.25), "Zoom in"),
      zbtn("Fit", () => canvas._fit(), "Fit to view"),
      zbtn("100%", () => canvas._setZoom(1), "Reset zoom")));

  return h("div", { class: "thread-canvas-wrap" }, picker, toolbar, readout, canvas);
}

let ctxMenuEl = null;
function closeCtxMenu() {
  if (ctxMenuEl) { ctxMenuEl.remove(); ctxMenuEl = null; }
}

function openThreadMenu(ev, chat, onRefresh) {
  closeCtxMenu();
  const meta = chat.meta || {};
  const item = (ic, label, fn, danger) => h("button", { class: `ctx-item${danger ? " danger" : ""}`, type: "button", role: "menuitem",
    on: { click: () => { closeCtxMenu(); fn(); } } }, h("span", { class: "ic" }, ic), label, h("span", {}));
  const menu = h("div", { class: "ctx-menu", role: "menu" },
    item(icon("pin"), meta.pinned ? "Unpin thread" : "Pin thread", () => threadAction(chat, "pin", !meta.pinned, onRefresh)),
    item(icon("check"), meta.settled ? "Reopen thread" : "Settle thread", () => threadAction(chat, "settle", !meta.settled, onRefresh)),
    item(icon("clock"), "Snooze for 1 hour", () => threadAction(chat, "snooze", 3600, onRefresh)),
    h("div", { class: "ctx-sep" }),
    item(icon("pencil"), "Rename", () => renameThread(chat, onRefresh)),
    item(icon("stack"), "Open canvas", () => openCanvas(chat.id)),
    item(icon("archive"), meta.archived ? "Unarchive" : "Archive thread", () => threadAction(chat, "archive", !meta.archived, onRefresh)),
    h("div", { class: "ctx-sep" }),
    item(icon("trash"), "Delete", () => confirmDelete(chat, onRefresh), true));
  document.body.append(menu);
  ctxMenuEl = menu;
  const pad = 8;
  let x = ev.clientX, y = ev.clientY;
  if (x + menu.offsetWidth + pad > window.innerWidth) x = window.innerWidth - menu.offsetWidth - pad;
  if (y + menu.offsetHeight + pad > window.innerHeight) y = window.innerHeight - menu.offsetHeight - pad;
  menu.style.left = `${Math.max(pad, x)}px`;
  menu.style.top = `${Math.max(pad, y)}px`;
  const onDoc = (e) => { if (!menu.contains(e.target)) { closeCtxMenu(); document.removeEventListener("pointerdown", onDoc); } };
  setTimeout(() => document.addEventListener("pointerdown", onDoc), 0);
}

async function threadAction(chat, action, value, onRefresh) {
  try {
    const res = await api("/api/thread_action", { id: chat.id, action, value });
    toast(res.result || "Done");
    if (res.meta) { threads.meta[chat.id] = res.meta; chat.meta = res.meta; }
    onRefresh(true);
  } catch (e) {
    toast(e.message);
  }
}

async function renameThread(chat, onRefresh) {
  const name = await ask("Rename thread", "Title");
  if (name == null) return;
  await threadAction(chat, "rename", name, onRefresh);
}

function confirmDelete(chat, onRefresh) {
  const dlg = modal("Delete thread", h("div", { class: "rows" },
    h("p", { class: "row-sub" }, `Delete \u201c${chat.title || chat.id}\u201d from the thread list? Its log stays on disk until retention removes it.`),
    h("div", { class: "btns" },
      h("button", { class: "btn danger", type: "button", on: { click: () => { dlg.close(); threadAction(chat, "delete", true, onRefresh); } } }, "Delete"),
      h("button", { class: "btn", type: "button", on: { click: () => dlg.close() } }, "Cancel"))));
}

export function threadLogsCard(data) {
  const err = data && data.error ? data.error : "";
  if (err) {
    return h("section", { class: "card thread-logs-card", "aria-labelledby": "thread-logs-title" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "thread-logs-title" }, "Thread Execution Logs")),
      h("div", { class: "empty" }, `Thread log error: ${err}`));
  }

  const threadsList = Array.isArray(data)
    ? data
    : (data && Array.isArray(data.threads) ? data.threads : []);

  if (!threadsList.length) {
    return h("section", { class: "card thread-logs-card", "aria-labelledby": "thread-logs-title" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "thread-logs-title" }, "Thread Execution Logs")),
      h("div", { class: "empty" }, "No thread execution logs recorded."));
  }

  const items = threadsList.map((t) => {
    const threadId = t.thread || "unknown";
    const recordCount = t.records != null ? t.records : 0;
    const updated = t.updated_at ? new Date(t.updated_at * 1000).toLocaleString() : "";

    const drawerBody = h("div", { class: "thread-log-drawer-body", style: "padding: 8px 0;" },
      h("div", { class: "row-sub" }, "Click to load log records, nodes, and edges…"));

    let loaded = false;
    const details = h("details", {
      class: "thread-log-item",
      on: {
        toggle: async () => {
          if (!details.open || loaded) return;
          drawerBody.replaceChildren(h("div", { class: "row-sub" }, "Loading execution log…"));
          try {
            const res = await get(`/api/thread_log_read?thread=${encodeURIComponent(threadId)}`);
            loaded = true;
            const nodes = res && res.nodes ? Object.entries(res.nodes) : [];
            const edges = res && Array.isArray(res.edges) ? res.edges : [];
            const records = res && Array.isArray(res.records) ? res.records : [];

            const nodeElements = nodes.length
              ? h("div", { class: "thread-log-nodes", style: "margin-bottom: 8px;" },
                  h("div", { class: "label" }, `Nodes (${nodes.length})`),
                  h("div", { class: "tags" }, nodes.map(([k, v]) => h("span", { class: "tag" }, `${k}: ${typeof v === "object" ? (v.label || v.role || JSON.stringify(v)) : v}`))))
              : null;

            const edgeElements = edges.length
              ? h("div", { class: "thread-log-edges", style: "margin-bottom: 8px;" },
                  h("div", { class: "label" }, `Edges (${edges.length})`),
                  h("div", { class: "tags" }, edges.map((e) => h("span", { class: "tag mono" }, `${e.source || e.from || "?"} → ${e.target || e.to || "?"}`))))
              : null;

            const recordElements = records.length
              ? h("div", { class: "thread-log-records" },
                  h("div", { class: "label" }, `Raw Records (${records.length})`),
                  ...records.map((r) => h("div", { class: "row mono row-sub", style: "white-space: pre-wrap; word-break: break-all;" }, typeof r === "string" ? r : JSON.stringify(r, null, 2))))
              : h("div", { class: "empty" }, "No records found in this log.");

            const drawerKids = [nodeElements, edgeElements, recordElements].filter(Boolean);
            const paged = pagedRegion(`thread-log-${threadId}`, `Thread ${threadId} log`, drawerKids);
            drawerBody.replaceChildren(paged);
          } catch (e) {
            drawerBody.replaceChildren(h("div", { class: "empty" }, `Failed to load thread log: ${e.message}`));
          }
        }
      }
    },
      h("summary", { class: "thread-log-summary", style: "cursor: pointer; display: flex; justify-content: space-between; align-items: center; padding: 6px 0;" },
        h("div", {},
          h("span", { class: "mono bold" }, threadId),
          updated ? h("span", { class: "row-sub", style: "margin-left: 8px;" }, updated) : null),
        h("div", { class: "tags" },
          h("span", { class: "tag num" }, `${recordCount} record${recordCount === 1 ? "" : "s"}`))),
      drawerBody);

    return details;
  });

  return h("section", { class: "card thread-logs-card", "aria-labelledby": "thread-logs-title" },
    h("div", { class: "card-head" },
      h("h2", { class: "card-title", id: "thread-logs-title" }, "Thread Execution Logs"),
      h("span", { class: "tag num" }, `${threadsList.length}`)),
    pagedRegion("thread-logs-list", "Thread execution logs list", items));
}
export const threadLogCard = threadLogsCard;

/* Thread exclusions fold away under the thread list; closed by default, and the state survives re-renders. */
let exclOpen = false;
function exclusionsFold(data, onRefresh) {
  const raw = (data && (data.exclusions || data)) || {};
  const n = (Array.isArray(raw.apps) ? raw.apps.length : 0) + (Array.isArray(raw.projects) ? raw.projects.length : 0);
  return h("details", { class: "card excl-fold", open: exclOpen, on: { toggle: (e) => { exclOpen = e.currentTarget.open; } } },
    h("summary", { class: "excl-summary" }, h("span", { class: "card-title" }, "Thread exclusions"), h("span", { class: "card-sub" }, n ? `${n} hidden` : "None")),
    exclusionsCard(data, onRefresh));
}

export function exclusionsCard(exclusionsData, onUpdate) {
  const data = exclusionsData || {};
  if (data.error) {
    return h("section", { class: "card exclusions-card", "aria-labelledby": "exclusions-title" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "exclusions-title" }, "Thread Exclusions")),
      h("div", { class: "empty" }, `Exclusions error: ${data.error}`));
  }

  const raw = data.exclusions || data;
  const apps = Array.isArray(raw.apps) ? raw.apps : [];
  const projects = Array.isArray(raw.projects) ? raw.projects : [];

  const post = async (newExclusions) => {
    try {
      await act("/api/exclusions", newExclusions);
      if (typeof onUpdate === "function") onUpdate();
    } catch (e) {
      toast(e.message);
    }
  };

  const renderSection = (title, placeholder, key, items) => {
    const input = h("input", {
      type: "text",
      value: "",
      placeholder,
      "aria-label": `Add ${title}`,
    });
    const add = () => {
      const val = input.value.trim();
      if (!val) return;
      if (items.includes(val)) { input.value = ""; return; }
      const updated = { apps: [...apps], projects: [...projects], [key]: [...items, val] };
      post(updated);
    };
    const remove = (val) => {
      const updated = { apps: [...apps], projects: [...projects], [key]: items.filter((x) => x !== val) };
      post(updated);
    };

    const list = items.length
      ? h("ul", { class: "rule-list" }, items.map((item) => h("li", { class: "rule-row" },
          h("code", { class: "rule-name" }, item),
          h("button", {
            class: "rule-remove",
            type: "button",
            "aria-label": `Remove ${item}`,
            title: `Remove ${item}`,
            on: { click: () => remove(item) },
          }, "\u00d7"))))
      : h("div", { class: "rule-empty row-sub" }, `No ${title.toLowerCase()} excluded.`);

    const form = h("form", { class: "rule-add", on: { submit: (e) => { e.preventDefault(); add(); } } },
      input,
      h("button", { class: "btn primary", type: "submit" }, "Add"));

    return h("div", { class: "exclusions-section", style: "margin-bottom: 12px;" },
      h("div", { class: "label" }, `${title} (${items.length})`),
      list,
      form);
  };

  let body;
  if (!apps.length && !projects.length) {
    body = [
      h("div", { class: "empty" }, "No exclusions defined. All chats are tracked."),
      renderSection("Apps", "e.g. Cursor, Claude", "apps", apps),
      renderSection("Project Paths", "e.g. /Users/name/work/secret", "projects", projects),
    ];
  } else {
    body = [
      renderSection("Apps", "e.g. Cursor, Claude", "apps", apps),
      renderSection("Project Paths", "e.g. /Users/name/work/secret", "projects", projects),
    ];
  }

  return h("section", { class: "card exclusions-card", "aria-labelledby": "exclusions-title" },
    h("div", { class: "card-head" },
      h("h2", { class: "card-title", id: "exclusions-title" }, "Thread Exclusions"),
      h("div", { class: "tags" },
        h("span", { class: "tag num" }, `${apps.length} app${apps.length === 1 ? "" : "s"}`),
        h("span", { class: "tag num" }, `${projects.length} project${projects.length === 1 ? "" : "s"}`))),
    h("div", { class: "card-sub", style: "margin-bottom: 8px;" }, "Chats from excluded applications or project directories are ignored by Subway."),
    pagedRegion("thread-exclusions", "Thread exclusions list", body));
}

export function threadsPage(d, chats, onRefresh, error = "") {
  const chatList = Array.isArray(chats) ? chats : [];
  const scopeBtn = h("button", { class: "btn" + (threads.scope === "all" ? " active" : ""), type: "button",
    "aria-pressed": String(threads.scope === "all"),
    on: { click: () => { threads.scope = threads.scope === "all" ? "subway" : "all"; onRefresh(); } } },
    threads.scope === "all" ? "All chats" : "Only chats that used Subway");
  const viewBtn = (key, label) => h("button", { type: "button", "aria-pressed": String(threads.view === key),
    on: { click: () => { threads.view = key; document.dispatchEvent(new Event("subway:rerender")); } } }, label);
  const viewSeg = h("div", { class: "seg", role: "group", "aria-label": "Thread view" }, viewBtn("image", "Image"), viewBtn("canvas", "Canvas"));

  const toolbar = h("div", { class: "threads-toolbar" },
    h("div", {}, h("h2", { class: "card-title" }, "Threads"),
      h("div", { class: "card-sub" }, "Chats that used Subway, and everything they started")),
    h("div", { class: "tools" }, scopeBtn, viewSeg, h("button", { class: "btn", type: "button", on: { click: onRefresh } }, "Refresh")));

  let body;
  if (error) body = h("div", { class: "err" }, `Could not load threads: ${error}`);
  else if (!chatList.length) body = trainEmpty();
  else if (threads.view === "canvas") body = threadCanvasView(chatList);
  else {
    for (const id of [...threadPanels.keys()]) {
      if (!chatList.some((c) => c.id === id)) threadPanels.delete(id);
    }
    body = h("div", { class: "thread-list" }, chatList.map((c) => threadCard(c, d, onRefresh)));
  }

  /* The composer belongs to the whole Threads page, in both views. Canvas view
     hands it the selected thread as context; Image view targets via @. */
  const canvasChat = threads.view === "canvas" ? (chatList.find((c) => c.id === threads.graphSel) || chatList[0] || null) : null;
  const composerEl = aiPrompt({ context: "threads", thread: canvasChat, chats: chatList });
  const tetherHost = h("div", { class: "tt-tether-host" });
  const composer = h("div", { class: "tt-composer-field", "data-thread": canvasChat || "" }, tetherHost, composerEl);
  return [
    h("section", { class: "card threads-card" }, toolbar, body, composer),
    threadLogsCard(d?.thread_logs || d?.thread_log),
    exclusionsFold(d?.exclusions, onRefresh),
  ];
}

/* Workers spun per day, for the overview terrain (fed by app.js). */
export const terrainState = { rows: [], loadedAt: 0, loading: false, error: "" };

/* Terminals */
export const terminalsState = { rows: [], sharing: false, loadedAt: 0, loading: false, error: "" };

function terminalRowTags(t) {
  const tags = [];
  if (t.closed) tags.push(h("span", { class: "tag bad" }, "closed"));
  if (t.source === "hook") {
    if (t.app === "terminal") {
      tags.push(h("span", { class: "tag" }, "Terminal.app"));
    } else if (t.app === "iterm") {
      tags.push(h("span", { class: "tag" }, "iTerm2"));
    } else {
      tags.push(h("span", { class: "tag" }, "shell hook: commands and exit codes, no output"));
    }
  }
  if (t.shell_integration === false) tags.push(h("span", { class: "tag" }, "no shell integration"));
  const canRun = t.source !== "hook" || t.app === "terminal" || t.app === "iterm";
  if (t.allow_commands && canRun) tags.push(h("span", { class: "tag" }, "commands allowed"));
  if (t.failures) tags.push(h("span", { class: "tag bad" }, `${t.failures} failing`));
  return tags;
}

async function openTerminalDetail(t) {
  const body = h("div", { class: "rows" }, h("div", { class: "empty" }, "Loading…"));
  const dlg = modal(t.name || t.id, body);
  try {
    const data = await api(`/api/terminal?id=${encodeURIComponent(t.id)}`);
    const rec = data.terminal || {};
    const commands = rec.commands || [];
    const rows = commands.length ? commands.slice().reverse().map((c) => {
      const failed = c.exit_code !== 0 && c.exit_code !== null && c.exit_code !== undefined;
      const outputPre = h("pre", { hidden: true }, c.output || "(no output)");
      const toggle = h("button", {
        class: "link-btn", type: "button",
        on: { click: () => { outputPre.hidden = !outputPre.hidden; } },
      }, "Show output");
      const handBtn = failed ? h("button", {
        class: "btn", type: "button",
        on: {
          click: () => {
            dlg.close();
            openDelegate(get("status"),
              `Terminal \`${rec.name || rec.id}\` ran \`${c.command}\` (exit ${c.exit_code}) in ${rec.folder || "an unknown folder"}:\n\n${(c.output || "").slice(0, 4000)}`);
          },
        },
      }, "Hand to worker") : null;
      return h("div", { class: `row${failed ? " alert" : ""}` },
        h("div", {},
          h("div", { class: "mono" }, `$ ${c.command}`),
          h("div", { class: "row-sub" }, `exit ${c.exit_code ?? "?"}${c.ts ? ` · ${new Date(c.ts * 1000).toLocaleString()}` : ""}`)),
        h("div", { class: "btns" }, toggle, handBtn),
        outputPre);
    }) : [h("div", { class: "empty" }, "No commands recorded yet.")];
    body.replaceChildren(...rows);
  } catch (e) {
    body.replaceChildren(h("div", { class: "err" }, `Could not load terminal: ${e.message}`));
  }
}

const terminalConfirm = new Set();
const terminalCaptureConfirm = new Set();
// Closed-log cleanup: which row(s) are asking for a delete confirmation. Survives re-renders.
const terminalClosedConfirm = new Set();
const CLOSED_ALL = "*all*";

function terminalClosedStamp(t) {
  if (typeof t.closed_at === "number" && t.closed_at > 0) return new Date(t.closed_at * 1000).toLocaleString();
  return terminalStamp(t);
}

function terminalInlineConfirm(kind, key, label, danger, question, onYes) {
  return h("div", { class: "term-confirm", role: "group", "aria-label": `${kind} ${label}` },
    h("div", { class: "term-confirm-text" }, question),
    h("div", { class: "btns" },
      h("button", {
        class: `btn ${danger ? "danger" : "primary"}`, type: "button",
        "aria-label": `${danger ? "Yes, delete" : "Yes, archive"} ${label}`,
        on: {
          click: () => {
            terminalClosedConfirm.delete(key);
            onYes();
          },
        },
      }, danger ? "Yes, delete" : "Yes, archive"),
      h("button", {
        class: "btn", type: "button", "aria-label": `Cancel ${kind.toLowerCase()} of ${label}`,
        on: {
          click: () => {
            terminalClosedConfirm.delete(key);
            rerenderTerminals();
          },
        },
      }, "Cancel")));
}

/* Closed terminals keep only their log: no switches, no tags, just open/archive/delete. */
function terminalClosedRow(t, onRefresh) {
  const name = t.name || t.id;
  const stamp = terminalClosedStamp(t);
  const last = t.last_command ? String(t.last_command) : "";
  const confirming = terminalClosedConfirm.has(t.id);
  const openLog = () => openTerminalDetail(t);
  return h("div", { class: "row term-row term-closed-row" },
    h("div", {
      class: "term-main", tabindex: "0", role: "button", "aria-label": `Open log for ${name}`,
      on: {
        click: openLog,
        keydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openLog(); } },
      },
    },
      h("b", { class: "row-title" }, name),
      last ? h("div", { class: "row-sub mono" }, `$ ${last}`) : null,
      h("div", { class: "row-sub" }, stamp ? `closed ${stamp}` : "closed")),
    h("div", { class: "term-controls" },
      h("div", { class: "btns" },
        h("button", {
          class: "btn", type: "button", "aria-label": `Archive ${name}`,
          on: { click: () => postTerminal("/api/terminal_log", { terminal_id: t.id, action: "archive" }, onRefresh) },
        }, "Archive"),
        h("button", {
          class: "btn danger", type: "button", "aria-label": `Delete ${name}`,
          on: {
            click: () => {
              terminalClosedConfirm.add(t.id);
              rerenderTerminals();
            },
          },
        }, "Delete")),
      confirming ? terminalInlineConfirm("Delete", t.id, name, true,
        `Delete the closed log for ${name}? This can't be undone.`,
        () => postTerminal("/api/terminal_log", { terminal_id: t.id, action: "delete" }, onRefresh)) : null));
}

function terminalClosedGroup(closed, onRefresh) {
  const confirmingAll = terminalClosedConfirm.has(CLOSED_ALL);
  return h("details", { class: "term-closed" },
    h("summary", {}, `Closed (${closed.length})`),
    h("div", { class: "term-closed-body" },
      h("div", { class: "btns" },
        h("button", {
          class: "btn", type: "button", "aria-label": `Archive all ${closed.length} closed terminals`,
          on: { click: () => postTerminal("/api/terminal_log", { all_closed: true, action: "archive" }, onRefresh) },
        }, "Archive all"),
        h("button", {
          class: "btn danger", type: "button", "aria-label": `Delete all ${closed.length} closed terminals`,
          on: {
            click: () => {
              terminalClosedConfirm.add(CLOSED_ALL);
              rerenderTerminals();
            },
          },
        }, "Delete all")),
      confirmingAll ? terminalInlineConfirm("Delete", CLOSED_ALL, `all ${closed.length} closed logs`, true,
        `Delete all ${closed.length} closed logs? This can't be undone.`,
        () => postTerminal("/api/terminal_log", { all_closed: true, action: "delete" }, onRefresh)) : null,
      h("div", { class: "rows" }, closed.map((t) => terminalClosedRow(t, onRefresh)))));
}

function terminalSwitch(label, checked, onToggle, disabled = false) {
  const btn = switchButton({
    checked: Boolean(checked), ariaLabel: label, size: "sm",
    onChange: () => { if (!disabled) onToggle(); },
  });
  if (disabled) { btn.disabled = true; btn.setAttribute("aria-disabled", "true"); }
  return h("div", { class: `tgl-field${disabled ? " disabled" : ""}` }, h("span", { class: "tgl-label" }, label), btn);
}

function postTerminal(path, body, onRefresh) {
  return act(path, body).finally(() => onRefresh());
}

function rerenderTerminals() {
  document.dispatchEvent(new Event("subway:rerender"));
}

function terminalStamp(t) {
  const at = t.updated_at || t.last_ts;
  return at ? new Date(at * 1000).toLocaleString() : "";
}

export function pendingRunsCard(runsData) {
  const data = runsData || {};
  if (data.error) {
    return h("section", { class: "card pending-runs-card", "aria-labelledby": "pending-runs-title" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "pending-runs-title" }, "Pending Terminal Runs")),
      h("div", { class: "empty" }, `Pending runs error: ${data.error}`));
  }

  const runsList = Array.isArray(data)
    ? data
    : (data && Array.isArray(data.runs) ? data.runs : []);

  if (!runsList.length) {
    return h("section", { class: "card pending-runs-card", "aria-labelledby": "pending-runs-title" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "pending-runs-title" }, "Pending Terminal Runs")),
      h("div", { class: "empty" }, "No pending terminal runs queued."));
  }

  const rows = runsList.map((r) => {
    const stamp = r.created_at ? new Date(r.created_at * 1000).toLocaleString() : "";
    const status = r.status || "pending";
    const statusClass = status === "running" ? "tag accent" : (status === "pending" ? "tag warning" : "tag");

    return h("div", { class: "row pending-run-row", style: "display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;" },
      h("div", { style: "min-width: 0; flex: 1 1 200px;" },
        h("div", { style: "display: flex; gap: 8px; align-items: center;" },
          h("b", { class: "mono" }, r.run_id || "run"),
          h("span", { class: "row-sub mono" }, `term: ${r.terminal_id || "unknown"}`),
          stamp ? h("span", { class: "row-sub" }, stamp) : null),
        r.command ? h("div", { class: "row-sub mono", style: "white-space: pre-wrap; word-break: break-all; margin-top: 4px;" }, `$ ${r.command}`) : null),
      h("div", { class: "tags" },
        h("span", { class: statusClass }, status)));
  });

  return h("section", { class: "card pending-runs-card", "aria-labelledby": "pending-runs-title" },
    h("div", { class: "card-head" },
      h("h2", { class: "card-title", id: "pending-runs-title" }, "Pending Terminal Runs"),
      h("span", { class: "tag num" }, `${runsList.length}`)),
    pagedRegion("pending-runs", "Pending terminal runs", rows));
}

export function terminalsPage(d, rows, onRefresh, error = "", loading = false, stale = false, sharing = false) {
  const all = rows || [];
  const list = all.filter((t) => !t.closed);
  const closed = all.filter((t) => Boolean(t.closed));
  const masterOn = Boolean(sharing);
  const sharingCard = h("section", { class: "card", "aria-labelledby": "terminal-sharing-title" },
    h("div", { class: "card-head" },
      h("div", {},
        h("h2", { class: "card-title", id: "terminal-sharing-title" }, "Terminal sharing"),
        h("div", { class: "card-sub" }, masterOn
          ? "On. Agents can read terminals you share below."
          : "Off. Agents can't see or use any terminal.")),
      terminalSwitch("Terminal sharing", masterOn, () => {
        if (masterOn) {
          terminalConfirm.clear();
          terminalCaptureConfirm.clear();
          terminalClosedConfirm.clear();
        }
        postTerminal("/api/terminal_sharing", { enabled: !masterOn }, onRefresh);
      })));
  const bodyRows = list.map((t) => {
    const shared = Boolean(t.share_enabled);
    const isHook = t.source === "hook";
    const isNativeHook = isHook && (t.app === "terminal" || t.app === "iterm");
    const isOtherHook = isHook && !isNativeHook;
    // A greyed-out switch can still be turned off; it only can't be turned on.
    const allowDisabled = (!masterOn || !shared || isOtherHook) && !t.allow_commands;
    const captureDisabled = (!masterOn || !shared) && !t.capture_output;
    const confirming = terminalConfirm.has(t.id) && !allowDisabled;
    const captureConfirming = terminalCaptureConfirm.has(t.id) && !captureDisabled;
    const stamp = terminalStamp(t);
    const sharedSwitch = terminalSwitch("Shared", shared, () => {
      if (shared) {
        terminalConfirm.delete(t.id);
        terminalCaptureConfirm.delete(t.id);
      }
      postTerminal("/api/terminal_opt_in", { terminal_id: t.id, share_enabled: !shared }, onRefresh);
    });
    const allowSwitch = terminalSwitch("Agents may run commands", Boolean(t.allow_commands), () => {
      if (t.allow_commands) {
        postTerminal("/api/terminal_opt_in", { terminal_id: t.id, allow_commands: false }, onRefresh);
      } else if (!terminalConfirm.has(t.id)) {
        terminalConfirm.add(t.id);
        rerenderTerminals();
      }
    }, allowDisabled);
    const captureSwitch = isNativeHook ? terminalSwitch("Capture output", Boolean(t.capture_output), () => {
      if (t.capture_output) {
        postTerminal("/api/terminal_opt_in", { terminal_id: t.id, capture_output: false }, onRefresh);
      } else if (!terminalCaptureConfirm.has(t.id)) {
        terminalCaptureConfirm.add(t.id);
        rerenderTerminals();
      }
    }, captureDisabled) : null;
    const hookNote = isOtherHook
      ? h("div", { class: "row-sub" }, "Needs an editor terminal with the Subway extension.")
      : null;
    const appLabel = t.app === "terminal" ? "Terminal.app" : "iTerm2";
    const macNote = isNativeHook
      ? h("div", { class: "row-sub" }, `macOS will ask once to let Subway control ${appLabel}.`)
      : null;
    const confirm = confirming ? h("div", {
      class: "term-confirm", role: "group", "aria-label": `Allow commands in ${t.name || t.id}`,
    },
      h("div", { class: "term-confirm-text" }, `Agents will be able to run commands in ${t.name || t.id}. Allow?`),
      h("div", { class: "btns" },
        h("button", {
          class: "btn primary", type: "button",
          on: {
            click: () => {
              terminalConfirm.delete(t.id);
              postTerminal("/api/terminal_opt_in", { terminal_id: t.id, allow_commands: true }, onRefresh);
            },
          },
        }, "Allow"),
        h("button", {
          class: "btn", type: "button",
          on: {
            click: () => {
              terminalConfirm.delete(t.id);
              rerenderTerminals();
            },
          },
        }, "Cancel"))) : null;
    const captureConfirm = captureConfirming ? h("div", {
      class: "term-confirm", role: "group", "aria-label": `Capture output in ${t.name || t.id}`,
    },
      h("div", { class: "term-confirm-text" }, "Subway will read this terminal's text after each command (secrets are redacted). Allow?"),
      h("div", { class: "btns" },
        h("button", {
          class: "btn primary", type: "button",
          on: {
            click: () => {
              terminalCaptureConfirm.delete(t.id);
              postTerminal("/api/terminal_opt_in", { terminal_id: t.id, capture_output: true }, onRefresh);
            },
          },
        }, "Allow"),
        h("button", {
          class: "btn", type: "button",
          on: {
            click: () => {
              terminalCaptureConfirm.delete(t.id);
              rerenderTerminals();
            },
          },
        }, "Cancel"))) : null;
    return h("div", { class: "row term-row" },
      h("div", {
        class: "term-main", tabindex: "0", role: "button",
        on: {
          click: () => openTerminalDetail(t),
          keydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openTerminalDetail(t); } },
        },
      },
        h("b", {}, t.name || t.id),
        h("div", { class: "row-sub" }, `${t.folder || "Unknown folder"}${stamp ? ` · ${stamp}` : ""}`),
        t.last_command ? h("div", { class: "row-sub mono" }, `$ ${t.last_command}`) : null),
      h("div", { class: "term-controls" },
        h("div", { class: "term-switches" }, sharedSwitch, h("div", {}, allowSwitch, hookNote), captureSwitch ? h("div", {}, captureSwitch) : null),
        macNote,
        h("div", { class: "tags" }, terminalRowTags(t)),
        confirm,
        captureConfirm));
  });
  return [
    sharingCard,
    pendingRunsCard(d?.pending_runs || d?.runs),
    h("section", { class: "card" },
      h("div", { class: "card-head" },
        h("div", {}, h("h2", { class: "card-title" }, "Terminals"),
          stale && !error ? h("span", { class: "tag" }, "stale") : null,
          h("div", { class: "card-sub" }, "Commands run in terminals your user has shared. Nothing is captured until they opt in.")),
        h("button", { class: "btn", type: "button", on: { click: onRefresh } }, "Refresh")),
      error ? h("div", { class: "err" }, `Could not refresh terminals: ${error}`) : null,
      closed.length && !bodyRows.length && !loading ? h("div", { class: "card-sub" }, "No open terminals; closed logs are under \"Closed\" below.") : null,
      h("div", { class: "rows" }, bodyRows.length ? bodyRows : h("div", { class: "empty" },
        loading && !closed.length ? "Loading…" : h("div", { class: "term-empty" },
          h("p", {}, "No terminals have been shared yet."),
          h("p", {}, "To start sharing, either:"),
          h("code", { class: "term-hint" }, "VS Code: run 'Subway: Share Terminal' in a terminal"),
          h("code", { class: "term-hint" }, "Shell: python3 server.py terminal hook install, then open a new terminal")))),
      closed.length ? terminalClosedGroup(closed, onRefresh) : null),
  ];
}

/* Ledger */
export const ledger = { track: "main", entries: [] };

export function renderLedgerEntries(entriesList) {
  const list = entriesList || ledger.entries;
  if (!list.length) return [h("div", { class: "empty" }, `Nothing on '${ledger.track}' yet.`)];
  return list.map((e) => h("div", {
    class: `entry${e.is_destructive ? " alert" : ""}`,
    tabindex: "0",
    role: "button",
    on: {
      click: () => openLedgerDetail(e),
      keydown: (ev) => {
        if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
          ev.preventDefault();
          openLedgerDetail(e);
        }
      },
    },
  },
    h("div", { class: "entry-head" }, h("span", { class: "mono" }, e.iso || ""), h("b", {}, e.station_id || "unknown"),
      e.session_id && e.session_id !== e.station_id ? h("span", { class: "tag" }, e.session_id) : null,
      e.unverified ? h("span", { class: "tag bad" }, "unverified") : null),
    h("div", {}, e.message || ""),
    (e.files || []).length ? h("div", { class: "row-sub" }, `Files: ${e.files.join(", ")}`) : null,
    (e.decisions || []).length ? h("div", { class: "row-sub" }, `Decision: ${e.decisions.join("; ")}`) : null));
}

function openLedgerDetail(e) {
  const content = h("div", { class: "dialog-body" },
    h("div", { class: "tags" },
      e.is_destructive ? h("span", { class: "tag bad" }, "destructive") : null,
      e.unverified ? h("span", { class: "tag bad" }, "unverified") : null,
      e.id ? h("span", { class: "tag mono" }, `id: ${e.id}`) : null,
      e.station_id ? h("span", { class: "tag" }, `station: ${e.station_id}`) : null,
      e.session_id ? h("span", { class: "tag" }, `session: ${e.session_id}`) : null,
      (e.iso || e.time) ? h("span", { class: "tag mono" }, e.iso || e.time) : null),
    h("div", {},
      h("div", { class: "label" }, "Message"),
      h("pre", {}, e.message || "(empty message)")),
    (e.files || []).length ? h("div", {},
      h("div", { class: "label" }, "Files"),
      h("ul", { class: "rows" }, e.files.map((f) => h("li", { class: "mono row-sub" }, f)))) : null,
    (e.decisions || []).length ? h("div", {},
      h("div", { class: "label" }, "Decisions"),
      h("ul", { class: "rows" }, e.decisions.map((d) => h("li", { class: "row-sub" }, d)))) : null);
  modal(e.message ? (e.message.length > 60 ? `${e.message.slice(0, 60)}…` : e.message) : "Ledger entry", content);
}

export function trackCard(trackData, onSwitch) {
  const data = trackData || {};
  if (data.error) {
    return h("section", { class: "card track-card", "aria-labelledby": "track-card-title" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title", id: "track-card-title" }, "Decision Track")),
      h("div", { class: "empty" }, `Track error: ${data.error}`));
  }

  const trackName = data.track || ledger.track || "main";
  const rawEntries = Array.isArray(data) ? data : (data.entries || ledger.entries || []);
  const tracks = data.tracks && typeof data.tracks === "object" ? Object.keys(data.tracks) : [trackName];
  if (!tracks.includes(trackName)) tracks.push(trackName);

  const trackSelector = tracks.length > 1 || onSwitch
    ? h("div", { class: "tabs", role: "tablist" }, tracks.map((t) => h("button", {
        class: "tab",
        type: "button",
        role: "tab",
        "aria-selected": String(t === trackName),
        on: onSwitch ? { click: () => onSwitch(t) } : {},
      }, `${t}${(data.tracks && data.tracks[t] && data.tracks[t].count) ? ` (${data.tracks[t].count})` : ""}`)))
    : null;

  let body;
  if (!rawEntries.length) {
    body = h("div", { class: "empty" }, "Track is currently empty. Broadcast decisions with broadcast_track().");
  } else {
    const renderedEntries = rawEntries.map((e) => {
      const meta = [
        e.iso ? h("span", { class: "mono" }, e.iso) : null,
        e.station_id ? h("b", {}, e.station_id) : null,
        e.session_id ? h("span", { class: "tag mono" }, e.session_id) : null,
        e.unverified ? h("span", { class: "tag bad" }, "unverified") : null,
      ].filter(Boolean);

      const files = (e.files || []).map((f) => h("span", { class: "tag mono" }, f));
      const decisions = (e.decisions || []).map((dec) => h("span", { class: "tag" }, dec));

      return h("div", {
        class: `entry${e.is_destructive ? " alert" : ""}`,
        tabindex: "0",
        role: "button",
        on: {
          click: () => openLedgerDetail(e),
          keydown: (ev) => {
            if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
              ev.preventDefault();
              openLedgerDetail(e);
            }
          },
        },
      },
        h("div", { class: "entry-head" }, meta),
        h("pre", { class: "entry-msg mono", style: "white-space: pre-wrap; margin: 6px 0;" }, e.message || ""),
        files.length ? h("div", { class: "row-sub", style: "display: flex; gap: 4px; align-items: center; flex-wrap: wrap;" },
          h("span", { class: "label", style: "margin: 0 4px 0 0;" }, "Files:"),
          h("div", { class: "tags" }, files)) : null,
        decisions.length ? h("div", { class: "row-sub", style: "display: flex; gap: 4px; align-items: center; flex-wrap: wrap; margin-top: 4px;" },
          h("span", { class: "label", style: "margin: 0 4px 0 0;" }, "Decisions:"),
          h("div", { class: "tags" }, decisions)) : null);
    });
    body = pagedRegion(`track-${trackName}`, `Track ${trackName} entries`, renderedEntries);
  }

  return h("section", { class: "card track-card", "aria-labelledby": "track-card-title" },
    h("div", { class: "card-head" },
      h("h2", { class: "card-title", id: "track-card-title" }, `Track: ${trackName}`),
      h("span", { class: "tag mono" }, `${rawEntries.length} entries`)),
    trackSelector,
    body);
}

export function ledgerPage(d, onSwitch) {
  const names = Object.keys(d.tracks || {});
  if (!names.includes(ledger.track)) names.push(ledger.track);
  const msg = h("input", { required: true, autocomplete: "off" });
  const files = h("input", { autocomplete: "off" });
  const trackData = d.track || { track: ledger.track, entries: ledger.entries, tracks: d.tracks };
  return [
    trackCard(trackData, onSwitch),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("h2", { class: "card-title" }, "Broadcast Decision"),
        h("button", { class: "btn", type: "button", on: { click: async () => {
          const name = (await ask("New track", "Track name") || "").toLowerCase().replace(/[^a-z0-9_-]/g, "");
          if (name) onSwitch(name);
        } } }, "New track")),
      h("form", { class: "form", on: { submit: async (e) => {
        e.preventDefault();
        const list = files.value.split(",").map((x) => x.trim()).filter(Boolean);
        if (await act("/api/broadcast", { message: msg.value, track: ledger.track, files: list, station_id: "hud-user" })) onSwitch(ledger.track);
      } } }, field("Post to this track", msg, true), field("Files, comma separated (optional)", files, true),
        h("div", { class: "btns field full" }, h("button", { class: "btn primary", type: "submit" }, "Post"))))
  ];
}
