/* hud/stats.js - reference skeleton handed to the builder; it may adjust details
   but must keep the exported signature and the /api/stats contract. */
import { h, api, icon, tokens, duration, providerColour } from "./lib.js";
import { liveLineChart, barChart } from "./charts.js";
import { activityTerrain } from "./terrain.js";

const METRICS = [
  { id: "tokens", label: "Tokens" },
  { id: "time", label: "Time" },
  { id: "speed", label: "Speed" },
  { id: "work", label: "Work" },
];
const MORE_METRICS = [
  { id: "tool_calls", label: "Tool calls" },
  { id: "thinking", label: "Thinking" },
  { id: "cache", label: "Cache" },
  { id: "failures", label: "Failures" },
];
const RANGES = [
  { id: "1h", label: "Past hour" },
  { id: "5h", label: "5 hours" },
  { id: "24h", label: "Past 24h" },
  { id: "7d", label: "7 days" },
  { id: "30d", label: "30 days" },
  { id: "90d", label: "90 days" },
];

const stats = { range: "24h", metric: "tokens", group: "provider", loading: false, error: "", data: null, offset: 0 };
let statsPageEl = null;
let statsChartEl = null;
let chartBodyEl = null;
let debounceTimer = null;
let statsSeq = 0;

const BUCKET_SECS = { "5min": 300, "15min": 900, "hour": 3600, "day": 86400 };

function fmt(unit, v) {
  if (v == null) return "—";
  if (unit === "tokens") return tokens(v);
  if (unit === "seconds") return `${Math.round(v)}s`;
  if (unit === "pct") return `${Math.round(v * 100)}%`;
  return String(v);
}

function setStatsOffset(next, debounce = false) {
  const want = Math.max(0, Math.round(next));
  if (want === stats.offset) return;
  stats.offset = want;
  if (debounce) {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      loadStats();
    }, 150);
  } else {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
    loadStats();
  }
}

async function loadStats() {
  stats.loading = true;
  if (!stats.data) paintStats();
  const seq = ++statsSeq;
  try {
    let url = `/api/stats?range=${stats.range}&metric=${stats.metric}&group=${stats.group}`;
    if (stats.offset > 0) {
      const bucketSec = (stats.data && BUCKET_SECS[stats.data.bucket]) || 3600;
      url += `&end=${Math.floor(Date.now() / 1000) - stats.offset * bucketSec}`;
    }
    const d = await api(url);
    if (seq !== statsSeq) return;
    stats.data = d;
    stats.error = "";
  } catch (e) {
    if (seq !== statsSeq) return;
    stats.error = String(e);
  }
  stats.loading = false;
  paintStats();
}

function seg(items, current, onPick, extra) {
  return h("div", { class: "seg stats-seg" }, items.map((it) =>
    h("button", { type: "button", "aria-pressed": String(it.id === current),
      on: { click: (e) => {
        // Move the highlight here: only the data below re-renders, not these buttons.
        for (const b of e.currentTarget.parentElement.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b === e.currentTarget));
        onPick(it.id);
      } } }, it.label)), extra || null);
}

let bodyEl = null;

function paintStats() {
  if (!bodyEl) return;
  const d = stats.data;
  if (stats.error) {
    bodyEl.replaceChildren(h("div", { class: "empty" }, `Couldn't load stats: ${stats.error}`));
    return;
  }
  if (!d || stats.loading) {
    bodyEl.replaceChildren(h("div", { class: "empty" }, "Loading…"));
    return;
  }
  const unit = d.unit || "tokens";
  const totalBig = h("div", { class: "mono stats-total" }, fmt(unit, d.total));
  const countLine = h("div", { class: "stats-count" }, `${tokens(d.count || 0)} ${d.count === 1 ? "session" : "sessions"}`);
  const providerRows = (d.by_provider || []).map((p) => h("div", { class: "stats-provider-row" },
    h("span", { class: "swatch", style: `background:${providerColour(p.id)}` }),
    h("span", { class: "stats-provider-name" }, p.name),
    h("span", { class: "stats-provider-count" }, `${tokens(p.count || 0)} sessions`),
    h("span", { class: "mono" }, fmt(unit, p.value)),
    h("span", { class: "stats-provider-share" }, `${(p.share || 0).toFixed(1)}% of ${unit}`)));
  const left = h("div", { class: "stats-left" }, totalBig, countLine,
    h("div", { class: "stats-provider-list" }, providerRows.length ? providerRows : h("div", { class: "empty" }, "No data for this range.")));

  const buckets = d.buckets || [];
  const labels = buckets.map((epoch) => new Date(epoch * 1000).toLocaleString(undefined,
    d.bucket === "day" ? { month: "short", day: "numeric" } : d.bucket === "hour" ? { hour: "numeric" } : { hour: "numeric", minute: "2-digit" }));
  const series = (d.series || []).map((sr) => ({ name: sr.name, color: providerColour(sr.id), values: sr.values }));
  const chartKey = `${stats.range}|${stats.metric}|${labels.length}|${labels[0] || ""}`;
  if (!series.length) {
    statsChartEl = null;
  } else if (statsChartEl && statsChartEl.__key === chartKey) {
    statsChartEl.update(series);
  } else {
    statsChartEl = liveLineChart({ series, labels, height: 250, format: (v) => fmt(unit, v), tip: stats.offset === 0 });
    statsChartEl.__key = chartKey;
  }

  const freq = ({ day: "Daily", hour: "Hourly", "5min": "Every 5 min", "15min": "Every 15 min" })[d.bucket] || "Hourly";
  let subText = `${freq} ${unit}`;
  if (stats.offset > 0 && buckets.length) {
    const endEpoch = buckets[buckets.length - 1];
    const endDate = new Date(endEpoch * 1000);
    const dateStr = endDate.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      ...(d.bucket === "5min" || d.bucket === "15min" ? { minute: "2-digit" } : {}),
    });
    subText = `${freq} ${unit} ending ${dateStr} (${stats.offset} back)`;
  }
  const subEl = h("div", { class: "card-sub" }, subText);

  const nowBtn = h("button", { class: "btn ghost spend-now", type: "button", title: "Back to latest" }, "Now");
  nowBtn.hidden = stats.offset === 0;
  nowBtn.addEventListener("click", () => {
    stats.offset = 0;
    loadStats();
  });

  const windowBuckets = Math.max(1, (buckets.length ? buckets.length - 1 : 24));
  const earlier = h("button", { class: "spend-step", type: "button", "aria-label": "Earlier", title: "Earlier" }, "\u2039");
  const later = h("button", { class: "spend-step", type: "button", "aria-label": "Later", title: "Later" }, "\u203a");
  earlier.disabled = false;
  later.disabled = stats.offset === 0;

  earlier.addEventListener("click", () => {
    setStatsOffset(stats.offset + windowBuckets, false);
  });
  later.addEventListener("click", () => {
    setStatsOffset(Math.max(0, stats.offset - windowBuckets), false);
  });

  const chartHead = h("div", { class: "spend-head" },
    subEl,
    h("div", { class: "spend-ctl" },
      h("span", { class: "spend-hint" }, "Drag or scroll to go back"),
      earlier, later, nowBtn));

  if (!chartBodyEl) {
    chartBodyEl = h("div", { class: "spend-body", tabindex: "0" });
    let drag = null;
    chartBodyEl.addEventListener("pointerdown", (e) => {
      if (e.button) return;
      drag = { x: e.clientX, offset: stats.offset, moved: false };
      try { chartBodyEl.setPointerCapture(e.pointerId); } catch { /* synthetic */ }
    });
    chartBodyEl.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x;
      if (!drag.moved && Math.abs(dx) < 4) return;
      drag.moved = true;
      chartBodyEl.classList.add("dragging");
      const currentBuckets = Math.max(1, (stats.data?.buckets?.length ? stats.data.buckets.length - 1 : 24));
      const colWidth = Math.max(1, chartBodyEl.clientWidth / currentBuckets);
      const cols = Math.round(dx / colWidth);
      setStatsOffset(drag.offset + cols, true);
    });
    const endDrag = () => {
      drag = null;
      chartBodyEl.classList.remove("dragging");
    };
    chartBodyEl.addEventListener("pointerup", endDrag);
    chartBodyEl.addEventListener("pointercancel", endDrag);
    let wheelAcc = 0;
    chartBodyEl.addEventListener("wheel", (e) => {
      const horizontal = Math.abs(e.deltaX) > Math.abs(e.deltaY);
      if (!horizontal && !e.shiftKey) return; // plain vertical scroll keeps scrolling the page
      e.preventDefault();
      wheelAcc += horizontal ? e.deltaX : e.deltaY;
      if (Math.abs(wheelAcc) < 40) return;
      setStatsOffset(stats.offset + (wheelAcc > 0 ? -1 : 1), true);
      wheelAcc = 0;
    }, { passive: false });
    chartBodyEl.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") { e.preventDefault(); setStatsOffset(stats.offset + 1, false); }
      if (e.key === "ArrowRight") { e.preventDefault(); setStatsOffset(stats.offset - 1, false); }
    });
  }

  chartBodyEl.replaceChildren(statsChartEl || h("div", { class: "empty" }, "No data for this range."));
  const right = h("div", { class: "stats-right" }, chartHead, chartBodyEl);

  const totals = d.totals || {};
  const totalsRow = h("div", { class: "stats-totals-row" },
    [["Processed tokens", totals.processed], ["Cached input", totals.cached_input], ["Uncached input", totals.uncached_input],
      ["Output", totals.output], ["Thinking", totals.thinking]].map(([label, v]) =>
      h("div", { class: "stats-tile" }, h("div", { class: "stats-tile-label" }, label), h("div", { class: "mono stats-tile-value" }, v == null ? "Unknown" : tokens(v)))));

  const byType = d.by_type || {};
  const typeRows = Object.entries(byType).filter(([, v]) => v != null).map(([k, v]) => ({
    id: k, label: k, value: v, color: k === "input" ? "var(--accent)" : k === "output" ? "var(--green)" : k === "cache_read" ? "var(--amber)" : "var(--muted)",
  }));
  const typeChart = typeRows.length ? barChart({ id: "stats-by-type", orientation: "horizontal", rows: typeRows, format: (v) => tokens(v), emptyText: "No data for this range." }) : h("div", { class: "empty" }, "No data for this range.");

  const breakdownRows = (d.breakdown || []).map((b, i) => h("tr", {},
    h("td", {}, String(i + 1)),
    h("td", {}, h("span", { class: "swatch", style: `background:${providerColour(b.provider)}` }), b.model,
      h("div", { class: "stats-share-bar" }, h("div", { class: "stats-share-fill", style: `width:${Math.round(b.share || 0)}%; background:${providerColour(b.provider)}` }))),
    h("td", { class: "mono" }, fmt(unit, b.value)),
    h("td", {}, `${Math.round(b.share || 0)}%`),
    h("td", { class: "mono" }, tokens(b.count || 0))));
  const breakdown = h("div", { class: "stats-breakdown" },
    h("div", { class: "card-sub" }, "Breakdown"),
    breakdownRows.length
      ? h("table", { class: "stats-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Model"), h("th", {}, unit), h("th", {}, "Share"), h("th", {}, "Count"))),
          h("tbody", {}, breakdownRows))
      : h("div", { class: "empty" }, "No data for this range."));

  const sessionsList = d.sessions || [];
  const maxSessCache = Math.max(...sessionsList.map((s) => s.cache_read || 0), 1);
  const pTotal = d.principal_cache_read || 0;
  const wTotal = d.worker_cache_read || 0;
  const ratio = wTotal > 0 ? `${(pTotal / wTotal).toFixed(1)}:1` : (pTotal > 0 ? "1:0" : "—");

  const sessionRows = sessionsList.map((s) => {
    const sharePct = Math.round(((s.cache_read || 0) / maxSessCache) * 100);
    const isRunaway = (s.peak_ctx || 0) >= 200000;
    const ago = s.last_ts ? `${duration(Math.max(0, Date.now() / 1000 - s.last_ts))} ago` : "—";
    return h("tr", isRunaway ? { class: "is-runaway" } : {},
      h("td", {},
        h("span", { class: "swatch", style: `background:${providerColour(s.provider)}` }),
        s.title || s.id,
        " ",
        h("span", { class: "mono", style: "color:var(--muted); font-size:0.75rem;" }, s.id),
        h("div", { class: "stats-share-bar" }, h("div", { class: "stats-share-fill", style: `width:${sharePct}%; background:${providerColour(s.provider)}` }))),
      h("td", { class: "mono" }, s.kind || "unknown"),
      h("td", { class: "mono" }, tokens(s.calls || 0)),
      h("td", { class: "mono" }, tokens(s.cache_read || 0)),
      h("td", { class: "mono" },
        tokens(s.peak_ctx || 0),
        isRunaway ? h("span", { class: "stats-runaway-pill" }, "runaway") : null),
      h("td", { class: "mono" }, ago));
  });

  const sessionsSection = h("div", { class: "stats-breakdown stats-sessions" },
    h("div", { class: "card-title" }, "Sessions"),
    h("div", { class: "card-sub" }, `Principals ${tokens(pTotal)} · Workers ${tokens(wTotal)} (${ratio})`),
    sessionRows.length
      ? h("table", { class: "stats-table" },
          h("thead", {}, h("tr", {},
            h("th", {}, "Session"),
            h("th", {}, "Kind"),
            h("th", {}, "Calls"),
            h("th", {}, "Cache read"),
            h("th", {}, "Peak context"),
            h("th", {}, "Last active"))),
          h("tbody", {}, sessionRows))
      : h("div", { class: "empty" }, "No data for this range."));

  bodyEl.replaceChildren(
    h("div", { class: "stats-cols" }, left, right),
    totalsRow, typeChart, sessionsSection, breakdown);
}

export function statsPage(d) {
  if (statsPageEl) return statsPageEl;
  const refreshBtn = h("button", { class: "icon-btn", type: "button", "aria-label": "Refresh" }, icon("refresh"));
  refreshBtn.addEventListener("click", () => loadStats());
  const metricSeg = seg(METRICS, stats.metric, (id) => { stats.offset = 0; stats.metric = id; moreSel.value = ""; moreSel.removeAttribute("data-active"); loadStats(); });
  const moreSel = h("select", { class: "stats-more", "aria-label": "More metrics" },
    h("option", { value: "" }, "More…"), MORE_METRICS.map((m) => h("option", { value: m.id }, m.label)));
  moreSel.addEventListener("change", () => {
    if (!moreSel.value) return;
    stats.offset = 0;
    stats.metric = moreSel.value;
    // A "More" metric is the one in focus now: clear the main buttons, mark the select.
    for (const btn of metricSeg.querySelectorAll("button")) btn.setAttribute("aria-pressed", "false");
    moreSel.setAttribute("data-active", "true");
    loadStats();
  });
  const rangeSeg = seg(RANGES, stats.range, (id) => { stats.offset = 0; stats.range = id; loadStats(); });
  const head = h("div", { class: "stats-head" },
    h("div", {}, h("h2", { class: "card-title" }, "Stats"), h("div", { class: "card-sub" }, "Time, speed, work done, tokens and thinking, across every worker.")));
  const controls = h("div", { class: "stats-ctl" }, metricSeg, moreSel, rangeSeg, refreshBtn);
  const terrainSlot = h("section", { class: "card card-flush" }, h("div", { class: "terrain terrain-loading" }, "Loading worker activity…"));
  bodyEl = h("div", { class: "stats-body" }, h("div", { class: "empty" }, "Loading…"));
  statsPageEl = h("section", { class: "card stats-card" }, head, terrainSlot, controls, bodyEl);
  api("/api/worker_activity?days=365").then((data) => {
    const rows = (data && data.activity) || [];
    terrainSlot.replaceChildren(rows.length
      ? activityTerrain({ rows, endDate: rows[rows.length - 1].day, title: "Activity terrain", noun: "worker", verb: "spun" })
      : h("div", { class: "terrain terrain-loading" }, "No worker activity yet."));
  }).catch((e) => terrainSlot.replaceChildren(h("div", { class: "terrain terrain-loading" }, `Couldn't load worker activity: ${e.message || e}`)));
  loadStats();
  return statsPageEl;
}
