/* hud/diagnostics.js - System Diagnostics View */
import { h, api, icon } from "./lib.js";

const state = {
  loading: false,
  error: "",
  data: null,
  digest: null,
};

let pageEl = null;
let contentEl = null;
let refreshBtnEl = null;
let digestLoaded = false;

async function loadWorkerDigest() {
  if (digestLoaded) return;
  digestLoaded = true;
  try {
    const res = await api("/api/worker_digest");
    state.digest = (res && Array.isArray(res.items)) ? res.items : [];
  } catch (err) {
    state.digest = [];
  }
  updateView();
}

async function loadDiagnostics() {
  state.loading = true;
  state.error = "";
  updateView();
  try {
    const data = await api("/api/diagnostics");
    state.data = data;
    state.error = "";
  } catch (err) {
    state.error = (err && err.message) || String(err);
  } finally {
    state.loading = false;
    updateView();
  }
}

function normalizeChecks(payload) {
  if (!payload) return [];
  if (Array.isArray(payload.checks) && payload.checks.length > 0) {
    return payload.checks.map((c) => ({
      name: c.name || c.id || "Check",
      status: (c.status === "warn" || c.status === "warning") ? "warn" : (c.status === "fail" || c.status === "error" || c.ok === false) ? "fail" : "ok",
      detail: c.detail || c.message || c.summary || (c.ok === false ? "Check failed" : "Operational"),
      fix: c.fix || c.fix_hint || c.hint || null,
    }));
  }

  const checks = [];

  const collectors = (payload.collection && Array.isArray(payload.collection.collectors))
    ? payload.collection.collectors
    : [];

  for (const c of collectors) {
    let status = "ok";
    if (c.ok === false) {
      status = (c.name === "cpu_speed_limit") ? "warn" : "fail";
    }
    if (c.status) status = c.status;
    let detail = c.error || (c.ok ? "Collector responding nominally" : "Failed to collect metrics");
    if (c.detail) detail = c.detail;
    let fix = c.fix || c.fix_hint || c.hint || null;
    if (!fix && c.ok === false && c.name === "cpu_speed_limit") {
      fix = "Kernel limit metric not available on this platform architecture";
    }
    checks.push({
      name: c.name || "Collector",
      status,
      detail,
      fix,
    });
  }

  checks.unshift({
    name: "System Health",
    status: payload.healthy ? "ok" : "fail",
    detail: payload.healthy ? "All core system collectors reported healthy" : "Core collectors encountered errors",
    fix: payload.healthy ? null : "Review collector errors below",
  });

  if (payload.cpu_pct != null || payload.rss_bytes != null || payload.process_count != null) {
    const rssMb = payload.rss_bytes != null ? `${(payload.rss_bytes / (1024 * 1024)).toFixed(1)} MB` : "—";
    const cpu = payload.cpu_pct != null ? `${payload.cpu_pct}%` : "—";
    const procs = payload.process_count != null ? String(payload.process_count) : "—";
    checks.push({
      name: "Resource Consumption",
      status: (payload.cpu_pct != null && payload.cpu_pct > 95) ? "warn" : "ok",
      detail: `Processes: ${procs} | CPU: ${cpu} | RSS: ${rssMb}`,
      fix: (payload.cpu_pct != null && payload.cpu_pct > 95) ? "High CPU utilization detected; check worker tasks" : null,
    });
  }

  if (payload.host) {
    const hInfo = payload.host;
    const parts = [];
    if (hInfo.power_source) parts.push(`Power: ${hInfo.power_source}`);
    if (hInfo.thermal) parts.push(`Thermal: ${hInfo.thermal}`);
    if (hInfo.uptime) parts.push(`Uptime: ${hInfo.uptime}`);
    if (hInfo.low_power != null) parts.push(`Low power: ${hInfo.low_power ? "on" : "off"}`);
    checks.push({
      name: "Host Environment",
      status: (hInfo.thermal && hInfo.thermal !== "nominal") ? "warn" : "ok",
      detail: parts.join(" | ") || "Host telemetry nominal",
      fix: (hInfo.thermal && hInfo.thermal !== "nominal") ? "Device thermal throttling active; reduce concurrency" : null,
    });
  }

  return checks;
}

function renderContent() {
  if (state.loading && !state.data) {
    return h("div", { class: "diagnostics-loading", "aria-busy": "true" }, [
      icon("loader", 24),
      h("span", {}, "Gathering diagnostic probes..."),
    ]);
  }

  if (state.error && !state.data) {
    return h("div", { class: "diagnostics-error", role: "alert" }, [
      icon("alert-circle", 24),
      h("div", { class: "diagnostics-error-msg" }, `Diagnostics error: ${state.error}`),
      h("button", {
        type: "button",
        class: "diagnostics-refresh-btn",
        on: { click: () => loadDiagnostics() },
      }, "Retry"),
    ]);
  }

  const checks = normalizeChecks(state.data);
  if (!checks.length) {
    return h("div", { class: "diagnostics-empty" }, [
      icon("info", 24),
      h("span", {}, "No diagnostics checks reported"),
    ]);
  }

  return h("div", { class: "diagnostics-grid" }, checks.map((check) => {
    const status = check.status || "ok";
    const statusLabel = status.toUpperCase();
    const children = [
      h("div", { class: "diagnostics-card-head" }, [
        h("span", { class: "diagnostics-check-name" }, check.name),
        h("span", { class: "diagnostics-badge", "data-status": status }, statusLabel),
      ]),
      h("div", { class: "diagnostics-detail" }, check.detail || "No details provided"),
    ];

    if (check.fix) {
      children.push(h("div", { class: "diagnostics-fix" }, `Hint: ${check.fix}`));
    }

    return h("div", {
      class: "diagnostics-card",
      "data-status": status,
      "aria-label": `${check.name}: ${statusLabel}`,
    }, children);
  }));
}

function updateView() {
  if (!pageEl || !contentEl) return;
  if (refreshBtnEl) {
    refreshBtnEl.setAttribute("aria-busy", String(state.loading));
    refreshBtnEl.disabled = state.loading;
  }
  contentEl.replaceChildren(renderContent());
}

export function diagnosticsPage(d) {
  if (!pageEl) {
    contentEl = h("div", { class: "diagnostics-body" }, renderContent());
    refreshBtnEl = h("button", {
      type: "button",
      class: "diagnostics-refresh-btn",
      "aria-busy": String(state.loading),
      on: { click: () => loadDiagnostics() },
    }, [
      icon("refresh-cw", 14),
      h("span", {}, "Refresh"),
    ]);

    pageEl = h("div", { class: "diagnostics-container" }, [
      h("div", { class: "diagnostics-head" }, [
        h("div", { class: "diagnostics-title-group" }, [
          icon("activity", 20),
          h("h2", { class: "page-title" }, "System Diagnostics"),
        ]),
        refreshBtnEl,
      ]),
      contentEl,
    ]);

    loadDiagnostics();
  }
  return pageEl;
}
