import { api } from "./lib.js";

const FRIENDLY_LABELS = {
  worker_ended: "Worker finished",
  worker_failed: "Worker failed",
  budget_stopped: "Budget or guard stop",
  permission_needed: "Permission needed",
  approval_needed: "Approval needed",
  mail_fired: "Mail",
  minor: "Minor events",
};

const DEFAULT_ORDER = [
  "worker_ended",
  "worker_failed",
  "budget_stopped",
  "permission_needed",
  "approval_needed",
  "mail_fired",
  "minor",
];

const STATUS_LABELS = {
  supported: "Supported",
  can_wake: "Can wake the Mac",
  power_nap: "Power Nap",
  ready: "Ready",
  tailscale: "Tailscale",
};

let cache = null, err = "";
let inFlight = false;
let lastFetch = 0;
let lastOnDone = null;

export async function loadWake(onDone) {
  if (onDone) lastOnDone = onDone;
  if (inFlight) return;
  if (cache && Date.now() - lastFetch < 20000) return;
  inFlight = true;
  try {
    const [rules, status] = await Promise.all([
      api("/api/wake_rules"),
      api("/api/wake").catch(() => ({})),
    ]);
    cache = { rules, status };
    err = "";
    lastFetch = Date.now();
  } catch (e) {
    err = e.message || String(e);
  } finally {
    inFlight = false;
  }
  if (typeof onDone === "function") onDone();
}

export function wakeRuleRows(flag, save) {
  if (err) {
    return [{ label: "Couldn't load wake rules", detail: err }];
  }
  if (!cache) {
    return [{ label: "Loading…" }];
  }

  const rulesData = cache.rules || {};
  const universal = rulesData.universal || {};
  const effective = rulesData.effective || universal;
  const providerOverride = rulesData.provider_override || {};
  const threadOverride = rulesData.thread_override || {};
  const lockedSet = new Set(rulesData.locked || ["permission_needed"]);

  const keys = Array.from(
    new Set([
      ...DEFAULT_ORDER,
      ...Object.keys(universal),
      ...Object.keys(effective),
    ])
  );

  const doSave = (key, nextOn) => {
    if (typeof save === "function") {
      const res = save(key, nextOn);
      if (res && typeof res.then === "function") {
        res.then(() => {
          lastFetch = 0;
          return loadWake(lastOnDone);
        });
        return;
      }
    }
    api("/api/wake_rules", {
      rules: { [key]: { wake: nextOn } },
    })
      .then(() => {
        lastFetch = 0;
        return loadWake(lastOnDone);
      })
      .catch((e) => {
        err = e.message || String(e);
        if (lastOnDone) lastOnDone();
      });
  };

  const makeFlag = flag || ((id, label, checked, onSelect, detail) => ({ id, label, checked: Boolean(checked), onSelect, detail }));
  const rows = [];
  for (const key of keys) {
    const friendlyLabel = FRIENDLY_LABELS[key] || key;
    const rule = effective[key] || universal[key] || {};
    const effectiveOn = rule.wake !== false;
    const isLocked = lockedSet.has(key);

    let detail = undefined;
    if (isLocked) {
      detail = "Always on";
    } else {
      const overrideTargets = [];
      if (providerOverride && providerOverride[key]) overrideTargets.push("provider");
      if (threadOverride && threadOverride[key]) overrideTargets.push("thread");
      if (overrideTargets.length > 0) {
        detail = `Overridden for ${overrideTargets.join("/")}`;
      }
    }

    const onSelect = isLocked ? () => {} : () => doSave(key, !effectiveOn);
    const row = makeFlag(key, friendlyLabel, effectiveOn, onSelect, detail);
    if (isLocked) {
      row.disabled = true;
    }
    rows.push(row);
  }
  return rows;
}

export function wakeStatusRows() {
  if (err) {
    const errRows = [{ label: "Couldn't load wake rules", detail: err }];
    errRows.note = "";
    return errRows;
  }
  if (!cache) {
    const loadingRows = [{ label: "Loading…" }];
    loadingRows.note = "";
    return loadingRows;
  }

  const status = cache.status || {};
  const note = status.note || "";
  const rows = [];

  for (const [key, val] of Object.entries(status)) {
    if (key === "settings" || key === "note") continue;
    if (key === "fix" && (!val || (Array.isArray(val) && val.length === 0))) continue;

    const label = STATUS_LABELS[key] || key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
    let detail = "";

    if (typeof val === "boolean") {
      detail = val ? "Yes" : "No";
    } else if (key === "tailscale" && val && typeof val === "object" && val.installed === false) {
      detail = "Not installed";
    } else if (val && typeof val === "object") {
      if (Array.isArray(val)) {
        detail = val.join(" \u00b7 ");
      } else {
        const parts = Object.entries(val)
          .filter(([_, v]) => v !== "" && v != null)
          .map(([k, v]) => `${k}: ${v}`);
        detail = parts.join(" \u00b7 ") || "None";
      }
    } else {
      detail = String(val ?? "");
    }

    rows.push({ id: key, label, detail });
  }

  rows.note = note;
  return rows;
}

export function wakeStatusNote() {
  return (cache && cache.status && cache.status.note) || "";
}
