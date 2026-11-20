/* Release lanes state, cached fetcher, and UI components for hud_next. */
import { h } from "./lib.js";

export const LANES_UNLOCK_WARNING =
  "More lanes than this device can run well: expect swapping and slow workers.";

export const lanesState = {
  loaded: false,
  available: false, // stays false on 404 so UI degrades gracefully and hides
  loading: false,
  err: "",
  data: null, // { lanes, default, limit, recommend }
  lanes: [],
  defaultLane: "",
  limit: { limit: 0, recommended: 0, max_allowed: 6, unlocked: false },
  recommend: { recommended: 0, reason: "" },
  preflight: {}, // laneName -> { loading, err, data, notAvailable }
};

const listeners = new Set();
let inflight = null;
let lastFetchAt = 0;
let pollTimer = null;
let focusWired = false;
const POLL_MS = 30000;

export function subscribeLanes(fn) {
  if (typeof fn !== "function") return () => {};
  listeners.add(fn);
  ensureLifecycle();
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0 && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };
}

function notify() {
  for (const fn of Array.from(listeners)) {
    try { fn(lanesState); } catch (_) {}
  }
  try {
    window.dispatchEvent(new CustomEvent("subway:lanes", { detail: lanesState }));
  } catch (_) {}
}

function ensureLifecycle() {
  if (!focusWired && typeof window !== "undefined") {
    focusWired = true;
    window.addEventListener("focus", () => {
      if (document.visibilityState !== "hidden") loadLanes({ force: true });
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") loadLanes();
    });
  }
  if (!pollTimer && typeof window !== "undefined") {
    pollTimer = setInterval(() => {
      if (document.visibilityState !== "hidden") loadLanes({ force: true });
    }, POLL_MS);
  }
}

export async function loadLanes(opts = {}) {
  const force = Boolean(opts && opts.force);
  const now = Date.now();
  if (!force && lanesState.loaded && now - lastFetchAt < 15000) {
    return lanesState;
  }
  if (inflight) return inflight;

  lanesState.loading = true;
  ensureLifecycle();
  inflight = (async () => {
    try {
      const res = await fetch("/api/lanes", { cache: "no-store" });
      if (res.status === 404) {
        lanesState.available = false;
        lanesState.loaded = true;
        lanesState.err = "";
        lanesState.data = null;
        lanesState.lanes = [];
        lastFetchAt = Date.now();
        return lanesState;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      lanesState.available = true;
      lanesState.loaded = true;
      lanesState.err = "";
      lanesState.data = d;
      lanesState.lanes = Array.isArray(d.lanes) ? d.lanes : [];
      lanesState.defaultLane = d.default || "";
      lanesState.limit = d.limit || { limit: 0, recommended: 0, max_allowed: 6, unlocked: false };
      lanesState.recommend = d.recommend || { recommended: 0, reason: "" };
      lastFetchAt = Date.now();
    } catch (e) {
      lanesState.err = e.message || String(e);
      lanesState.loaded = true;
    } finally {
      lanesState.loading = false;
      inflight = null;
      notify();
    }
    return lanesState;
  })();
  return inflight;
}

export async function postLaneAction(body) {
  const res = await fetch("/api/lanes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  await loadLanes({ force: true });
  return data;
}

export async function saveLaneSettings(patch) {
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  await loadLanes({ force: true });
  return data;
}

export async function fetchShipPreflight(laneName) {
  lanesState.preflight[laneName] = { loading: true, err: "", data: null, notAvailable: false };
  notify();
  try {
    const res = await fetch(`/api/ship/preflight?lane=${encodeURIComponent(laneName)}`, {
      cache: "no-store",
    });
    if (res.status === 404) {
      lanesState.preflight[laneName] = {
        loading: false,
        err: "Preflight not available yet",
        data: null,
        notAvailable: true,
      };
    } else if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      lanesState.preflight[laneName] = {
        loading: false,
        err: d.error || `HTTP ${res.status}`,
        data: null,
        notAvailable: false,
      };
    } else {
      const d = await res.json();
      lanesState.preflight[laneName] = {
        loading: false,
        err: "",
        data: d,
        notAvailable: false,
      };
    }
  } catch (e) {
    lanesState.preflight[laneName] = {
      loading: false,
      err: e.message || String(e),
      data: null,
      notAvailable: false,
    };
  }
  notify();
  return lanesState.preflight[laneName];
}

export function clearShipPreflight(laneName) {
  delete lanesState.preflight[laneName];
  notify();
}

export function roleBadge(role) {
  const r = role || "dev";
  return h("span", { class: `ln-role ln-role-${r}` }, r);
}

export function laneDisplayLabel(lane) {
  if (!lane) return "";
  return `${lane.name} · ${lane.role || "dev"}`;
}

export function countLaneBranches(lane, branches) {
  const list = Array.isArray(branches) ? branches : [];
  const isMain = lane && lane.role === "main";
  return list.filter((w) => (w.lane ? w.lane === lane.name : isMain)).length;
}

function preflightItems(data) {
  if (!data || typeof data !== "object") return [];
  if (Array.isArray(data.checks)) return data.checks;
  if (Array.isArray(data.items)) return data.items;
  if (Array.isArray(data.steps)) return data.steps;
  return Object.entries(data)
    .filter(([k]) => !["lane", "ok", "ready", "summary", "error"].includes(k))
    .map(([k, v]) =>
      typeof v === "object" && v !== null
        ? { label: v.label || v.name || k, ok: v.ok ?? v.passed ?? v.status === "ok", detail: v.detail || v.message || "" }
        : { label: k, ok: Boolean(v), detail: typeof v === "string" ? v : "" }
    );
}

export function renderPreflightPanel(laneName) {
  const pf = lanesState.preflight[laneName];
  if (!pf) return null;
  const closeBtn = h(
    "button",
    {
      type: "button",
      class: "ld-btn",
      on: { click: () => clearShipPreflight(laneName) },
    },
    "Close"
  );
  let body;
  if (pf.loading) {
    body = h("div", { class: "ld-empty" }, "Running ship preflight…");
  } else if (pf.notAvailable) {
    body = h("div", { class: "ld-empty" }, "Preflight not available yet");
  } else if (pf.err) {
    body = h("div", { class: "ln-err" }, pf.err);
  } else {
    const items = preflightItems(pf.data);
    body = items.length
      ? h(
          "ul",
          { class: "ln-pf-list" },
          items.map((it) => {
            const ok = it.ok ?? it.passed ?? it.status === "ok" ?? it.status === "pass";
            const warn = it.status === "warn" || it.warn;
            const mark = ok ? "✓" : warn ? "!" : "✕";
            const cls = ok ? "ok" : warn ? "warn" : "bad";
            return h(
              "li",
              { class: `ln-pf-item ${cls}` },
              h("span", { class: "ln-pf-mark", "aria-hidden": "true" }, mark),
              h("span", { class: "ln-pf-label" }, it.label || it.name || it.id || "Check"),
              it.detail || it.message ? h("span", { class: "ln-pf-detail" }, it.detail || it.message) : null
            );
          })
        )
      : h("div", { class: "ld-empty" }, pf.data?.summary || (pf.data?.ready ? "All preflight checks passed." : "No preflight items returned."));
  }
  return h(
    "div",
    { class: "ln-preflight", "data-lane": laneName },
    h(
      "div",
      { class: "ln-pf-head" },
      h("strong", {}, `Ship preflight: ${laneName}`),
      closeBtn
    ),
    body
  );
}

export function mountLandingLanesStrip(slot, branches) {
  if (!slot) return;
  if (!lanesState.loaded) loadLanes();
  if (!lanesState.available || !lanesState.lanes.length) {
    slot.replaceChildren();
    slot.hidden = true;
    return;
  }
  slot.hidden = false;
  const activePreflights = Object.keys(lanesState.preflight).filter((k) => lanesState.preflight[k]);
  const cards = lanesState.lanes.map((lane) => {
    const branchCount = countLaneBranches(lane, branches);
    const isDefault = lane.name === lanesState.defaultLane;
    return h(
      "div",
      { class: `ln-card${lane.frozen ? " is-frozen" : ""}`, "data-lane": lane.name },
      h(
        "div",
        { class: "ln-card-top" },
        h(
          "div",
          { class: "ln-card-title" },
          h("span", { class: "ln-name" }, lane.name),
          roleBadge(lane.role),
          isDefault ? h("span", { class: "ld-tag ok" }, "Default") : null
        ),
        lane.role === "ship"
          ? h(
              "button",
              {
                type: "button",
                class: "ld-btn primary",
                disabled: Boolean(lanesState.preflight[lane.name]?.loading),
                on: { click: () => fetchShipPreflight(lane.name) },
              },
              lanesState.preflight[lane.name]?.loading ? "Checking…" : "Ship"
            )
          : null
      ),
      h("div", { class: "ln-branch" }, lane.branch || "—"),
      h(
        "div",
        { class: "ln-badges" },
        h("span", { class: "ld-tag" }, `${Number(lane.ahead_main || 0)} ahead`),
        h("span", { class: "ld-tag" }, `${branchCount} unmerged`),
        lane.dirty ? h("span", { class: "ln-dirty-pill" }, h("span", { class: "ln-dirty-dot" }), "Dirty") : null,
        lane.frozen ? h("span", { class: "ld-tag bad" }, "Frozen") : null,
        lane.missing ? h("span", { class: "ld-tag bad" }, "Missing worktree") : null
      )
    );
  });

  const sec = h(
    "section",
    { class: "ld-card ln-strip-card", "aria-label": "Lanes" },
    h(
      "div",
      { class: "ld-head" },
      h(
        "div",
        {},
        h("h2", {}, "Lanes"),
        h("div", { class: "ld-sub" }, "Active release and development lanes in this repository.")
      )
    ),
    h("div", { class: "ln-strip" }, cards),
    ...activePreflights.map((name) => renderPreflightPanel(name))
  );
  const shipSlot = h("div", { class: "ln-ship-slot" });
  sec.prepend(shipSlot);
  window.SubwayShip?.mountCards?.(shipSlot, lanesState.lanes.map((l) => l.name), lanesState.lanes.filter((l) => l.role === "release").map((l) => l.name));
  slot.replaceChildren(sec);
}

let settingsErr = "";
let settingsBusy = false;
let formState = { name: "", base: "", role: "dev" };

export function fillSettingsSlot(page, settingsObj) {
  if (!page) return;
  const slot = page.querySelector("[data-lanes-slot]");
  if (!slot) return;
  if (!lanesState.loaded) {
    loadLanes();
    slot.replaceChildren();
    return;
  }
  if (!lanesState.available) {
    const sec = slot.closest(".st-sec");
    if (sec) sec.hidden = true;
    slot.replaceChildren();
    return;
  }
  const sec = slot.closest(".st-sec");
  if (sec) sec.hidden = false;

  const s = settingsObj || {};
  const rec = lanesState.recommend || {};
  const lim = lanesState.limit || {};
  const recCount = rec.recommended ?? lim.recommended ?? 0;
  const maxAllowed = Number(lim.max_allowed || 6);
  const unlocked = Boolean(s.lanes_unlocked ?? lim.unlocked);
  const boundMax = unlocked ? 64 : maxAllowed;
  const currentMax = Number(s.lanes_max ?? 0);
  const defLane = s.lanes_default || lanesState.defaultLane || "";

  const run = async (fn) => {
    settingsBusy = true;
    settingsErr = "";
    fillSettingsSlot(page, s);
    try {
      await fn();
    } catch (e) {
      settingsErr = e.message || String(e);
    } finally {
      settingsBusy = false;
      fillSettingsSlot(page, s);
    }
  };

  const recLine = h(
    "div",
    { class: "ln-rec" },
    `Recommended for this device: ${recCount}${rec.reason ? ` — ${rec.reason}` : ""}`
  );

  const laneRows = lanesState.lanes.length
    ? lanesState.lanes.map((lane) => {
        const isDefault = lane.name === defLane;
        const frozen = Boolean(lane.frozen);
        return h(
          "div",
          { class: "ln-st-row", "data-lane": lane.name },
          h(
            "div",
            { class: "ln-st-main" },
            h(
              "div",
              { class: "ln-st-title" },
              lane.dirty ? h("span", { class: "ln-dirty-dot", title: "Dirty working tree" }) : null,
              h("span", { class: "ln-name" }, lane.name),
              roleBadge(lane.role),
              isDefault ? h("span", { class: "ld-tag ok" }, "Default") : null,
              lane.missing ? h("span", { class: "ld-tag bad" }, "Missing") : null
            ),
            h(
              "div",
              { class: "ln-st-meta" },
              `${lane.branch || "—"} · ${Number(lane.ahead_main || 0)} ahead of main`
            )
          ),
          h(
            "div",
            { class: "ln-st-acts" },
            h(
              "button",
              {
                type: "button",
                class: "ld-btn",
                "aria-pressed": String(frozen),
                disabled: settingsBusy,
                on: {
                  click: () =>
                    run(() => postLaneAction({ action: "freeze", name: lane.name, frozen: !frozen })),
                },
              },
              frozen ? "Unfreeze" : "Freeze"
            ),
            !isDefault
              ? h(
                  "button",
                  {
                    type: "button",
                    class: "ld-btn",
                    disabled: settingsBusy,
                    on: {
                      click: () =>
                        run(async () => {
                          await postLaneAction({ action: "default", name: lane.name });
                          s.lanes_default = lane.name;
                        }),
                    },
                  },
                  "Set default"
                )
              : null,
            lane.role !== "main"
              ? h(
                  "button",
                  {
                    type: "button",
                    class: "ld-btn danger",
                    disabled: settingsBusy,
                    on: {
                      click: () => {
                        if (!window.confirm(`Remove lane "${lane.name}"?`)) return;
                        run(() => postLaneAction({ action: "remove", name: lane.name }));
                      },
                    },
                  },
                  "Remove"
                )
              : null
          )
        );
      })
    : [h("div", { class: "ld-empty" }, "No lanes configured yet.")];

  const baseOpts = [
    h("option", { value: "" }, "Default / HEAD"),
    ...lanesState.lanes.map((l) => h("option", { value: l.name }, `${l.name} (${l.branch || l.role})`)),
  ];

  const newForm = h(
    "form",
    {
      class: "ln-form",
      on: {
        submit: (e) => {
          e.preventDefault();
          const name = formState.name.trim();
          if (!name) {
            settingsErr = "Enter a lane name.";
            fillSettingsSlot(page, s);
            return;
          }
          run(async () => {
            const payload = { action: "create", name, role: formState.role || "dev" };
            if (formState.base) payload.base = formState.base;
            await postLaneAction(payload);
            formState = { name: "", base: "", role: "dev" };
          });
        },
      },
    },
    h("div", { class: "ln-form-title" }, "New lane"),
    h(
      "div",
      { class: "ln-form-grid" },
      h("input", {
        type: "text",
        class: "st-text ln-input",
        placeholder: "Lane name (e.g. 1.3.10)",
        "aria-label": "New lane name",
        value: formState.name,
        on: { input: (e) => { formState.name = e.target.value; } },
      }),
      h(
        "select",
        {
          class: "st-select",
          "aria-label": "Base lane",
          value: formState.base,
          on: { change: (e) => { formState.base = e.target.value; } },
        },
        baseOpts
      ),
      h(
        "select",
        {
          class: "st-select",
          "aria-label": "Lane role",
          value: formState.role,
          on: { change: (e) => { formState.role = e.target.value; } },
        },
        h("option", { value: "dev" }, "dev"),
        h("option", { value: "ship" }, "ship"),
        h("option", { value: "main" }, "main")
      ),
      h("button", { type: "submit", class: "ld-btn primary", disabled: settingsBusy }, "Create")
    )
  );

  const maxInput = h("input", {
    type: "number",
    class: "st-text ln-num",
    min: "0",
    max: String(boundMax),
    step: "1",
    value: String(Math.max(0, Math.min(boundMax, Number.isFinite(currentMax) ? currentMax : 0))),
    "aria-label": "Max active lanes (0 = recommended)",
    disabled: settingsBusy,
    on: {
      change: (e) => {
        let val = Math.round(Number(e.target.value));
        if (!Number.isFinite(val) || val < 0) val = 0;
        if (val > boundMax) val = boundMax;
        e.target.value = String(val);
        run(async () => {
          await saveLaneSettings({ lanes_max: val });
          s.lanes_max = val;
        });
      },
    },
  });

  const unlockBtn = h(
    "button",
    {
      type: "button",
      class: "st-sw",
      role: "switch",
      "aria-checked": String(unlocked),
      "aria-label": "Unlock lane limit",
      disabled: settingsBusy,
      on: {
        click: () => {
          const next = !unlocked;
          if (next && !window.confirm(LANES_UNLOCK_WARNING)) return;
          run(async () => {
            const patch = { lanes_unlocked: next };
            if (!next && currentMax > maxAllowed) {
              patch.lanes_max = maxAllowed;
              s.lanes_max = maxAllowed;
            }
            await saveLaneSettings(patch);
            s.lanes_unlocked = next;
          });
        },
      },
    },
    h("span", { class: "st-sw-k" })
  );

  const limitBox = h(
    "div",
    { class: "ln-limit" },
    h(
      "div",
      { class: "ln-st-row" },
      h(
        "div",
        { class: "ln-st-main" },
        h("div", { class: "st-label" }, "Max lanes (0 = recommended)"),
        h("div", { class: "st-detail" }, `Bounded 0..${boundMax}`)
      ),
      maxInput
    ),
    h(
      "div",
      { class: "ln-st-row" },
      h(
        "div",
        { class: "ln-st-main" },
        h("div", { class: "st-label" }, "Unlock higher lane limit"),
        h("div", { class: "st-detail" }, "Allows up to 64 lanes instead of device cap")
      ),
      unlockBtn
    ),
    unlocked ? h("div", { class: "ln-warn", role: "alert" }, LANES_UNLOCK_WARNING) : null
  );

  const root = h(
    "div",
    { class: "ln-settings" },
    recLine,
    settingsErr ? h("div", { class: "ln-err", role: "alert" }, settingsErr) : null,
    h("div", { class: "ln-st-list" }, laneRows),
    newForm,
    limitBox
  );
  slot.replaceChildren(root);
}

if (typeof window !== "undefined") {
  window.SubwayLanes = {
    lanesState,
    loadLanes,
    subscribeLanes,
    postLaneAction,
    saveLaneSettings,
    fetchShipPreflight,
    clearShipPreflight,
    mountLandingLanesStrip,
    fillSettingsSlot,
    laneDisplayLabel,
    countLaneBranches,
    LANES_UNLOCK_WARNING,
  };
}
