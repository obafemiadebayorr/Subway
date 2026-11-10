/* LLM usage: rings, provider cards, and the spend chart. Every value comes from the API. */
import { h, s, tokens, pct, ticking, providerColour } from "./lib.js";
import { notchGauge } from "./charts.js";

const STATE = { live: ["Live", "ok"], stale: ["Stale", "bad"], unavailable: ["No data", "bad"], detected: ["Installed", ""] };

function arc(radius, left, cls) {
  const c = 2 * Math.PI * radius;
  return [
    s("circle", { class: "track", cx: 48, cy: 48, r: radius, fill: "none", "stroke-width": 8 }),
    typeof left === "number" ? s("circle", {
      class: `arc ${cls}${left <= 10 ? " low" : ""}`, cx: 48, cy: 48, r: radius, fill: "none", "stroke-width": 8, "stroke-linecap": "round",
      "stroke-dasharray": c, "stroke-dashoffset": c * (1 - Math.min(100, Math.max(0, left)) / 100), transform: "rotate(-90 48 48)",
    }) : null,
  ];
}

const usable = (w, now) => !w.disabled && typeof w.left_pct === "number" && !(w.resets_at && w.resets_at <= now);

/* Notch gauge with two concentric rings: outer = the session window (yellow), inner = the weekly
   window (blue), the same colours as the 5-hour and weekly rings elsewhere. */
export function ring(pool, now) {
  const live = pool.windows.filter((w) => usable(w, now));
  const lowestWin = live.reduce((min, w) => (min === null || w.left_pct < min.left_pct ? w : min), null);
  const lowest = lowestWin === null ? null : lowestWin.left_pct;
  const label = pool.windows.map((w) => `${w.label} ${usable(w, now) ? pct(w.left_pct) + " left" : "not reported"}`).join(", ");
  const RING_COLOURS = [{ from: "#FFD43B", to: "#F5C518" }, { from: "#5BB8E8", to: "#3f9fd0" }];
  const rings = pool.windows.map((w, i) => {
    const colour = RING_COLOURS[i] || RING_COLOURS[RING_COLOURS.length - 1];
    return {
      value: usable(w, now) ? w.left_pct : 0, max: 100,
      radius: i === 0 ? 86 : 64, thickness: 15,
      from: colour.from, to: colour.to,
      track: usable(w, now) ? "var(--line)" : "var(--line-soft)",
    };
  });
  const gauge = notchGauge({
    rings,
    value: lowest === null ? 0 : lowest,
    max: 100,
    label: pool.name,
    caption: lowestWin ? `${lowestWin.label} left` : "",
    format: (v) => `${Math.round(v)}%`,
  });
  gauge.setAttribute("role", "img");
  gauge.setAttribute("aria-label", label);
  return gauge;
}

function windowRow(w, i, now) {
  let value;
  if (w.disabled) value = h("span", { class: "legend-val quiet" }, "Not applied now");
  else if (w.left_pct === null || w.left_pct === undefined) value = h("span", { class: "legend-val quiet" }, "Not reported");
  else if (w.resets_at && w.resets_at <= now) value = h("span", { class: "legend-val quiet" }, "Reset, awaiting reading");
  else value = h("span", { class: `legend-val${w.left_pct <= 10 ? " low" : ""}` }, `${pct(w.left_pct)} left`);
  const when = w.resets_at && w.resets_at > now && !w.disabled;
  return [
    h("div", { class: "legend-row" }, h("span", { class: "legend-key" }, h("span", { class: `swatch${i ? " two" : ""}` }), w.label), value),
    when ? h("div", { class: "row-sub" }, "Resets in ", ticking("span", { class: "num" }, w.resets_at, "countdown")) : null,
  ];
}

function tokenTable(t) {
  if (!t || typeof t !== "object") return null;  /* providers that report a scalar total have no windows */
  const row = (label, v) => [h("span", { class: "rowlabel" }, label), h("b", {}, tokens(v.input)), h("b", {}, tokens(v.output)), h("b", {}, tokens(v.cache_read))];
  return h("div", { class: "tokens", "aria-label": "Tokens used" },
    h("span", { class: "h" }, "Tokens"), h("span", { class: "h" }, "In"), h("span", { class: "h" }, "Out"), h("span", { class: "h" }, "Cache"),
    row("5h", t["5h"] || {}), row("7d", t["7d"] || {}));
}

function chips(c, isLocalCapable) {
  const out = [];
  if (c.signed_in === true) out.push(h("span", { class: "tag ok" }, "Signed in"));
  if (c.signed_in === false) out.push(h("span", { class: "tag bad" }, "Not signed in"));
  if (c.sessions > 0) out.push(h("span", { class: "tag ok" }, `Subway connected (${c.sessions})`));
  else if (c.registered) out.push(h("span", { class: "tag" }, "Subway registered"));
  else if (c.dispatchable && !isLocalCapable) out.push(h("span", { class: "tag" }, "Subway not installed"));
  if (c.dispatchable) out.push(h("span", { class: "tag accent" }, "Can run workers"));
  return out;
}

const gb = (bytes) => `${(bytes / 1073741824).toFixed(1)} GB`;

function memoryBlock(p) {
  const m = p.memory;
  const used = Math.max(0, m.total - m.available);
  const usedPct = Math.min(100, Math.max(0, (used / m.total) * 100));
  const hot = usedPct >= 90;
  const gauge = notchGauge({
    value: used, max: m.total,
    caption: `${gb(p.room)} safe`,
    size: 200, radius: 80, thickness: 15,
    from: hot ? "#f0605a" : "#5BB8E8", to: hot ? "#f0a94a" : "#5094d1",
    format: () => gb(used),
  });
  gauge.setAttribute("role", "img");
  gauge.setAttribute("aria-label", `${gb(m.available)} free of ${gb(m.total)}; ${gb(p.room)} safe for models`);
  return h("div", { class: "legend" },
    h("div", { class: "legend-row" }, h("span", { class: "legend-key" }, "Safe for models now"), h("span", { class: "legend-val" }, gb(p.room))),
    gauge,
    h("div", { class: "row-sub" }, `${gb(m.available)} free of ${gb(m.total)}${m.swap_used ? `, ${gb(m.swap_used)} in swap` : ""}`),
    (p.loaded || []).map((l) => h("div", { class: "legend-row" }, h("span", { class: "legend-key wrap" }, "Loaded: ", l.name), h("span", { class: "legend-val" }, gb(l.bytes)))),
    (p.models || []).length ? h("details", {}, h("summary", {}, `${p.models.length} installed model${p.models.length === 1 ? "" : "s"}`),
      h("ul", {}, p.models.map((x) => h("li", {}, `${x.name}: ${x.fits ? "fits" : "too big right now"}, about ${gb(x.needs)}${x.measured ? "" : " (estimated)"}`)))) : null);
}

/* DeepAI bills in Theos: a daily pot and a published price per request. There is no
   percentage to show, so the card shows the measured spend and nothing else. */
function theosBlock(p) {
  const t = (p.tokens && p.tokens.theos) || p.theos;
  if (!t || typeof t !== "object" || !Array.isArray(t.models) || !t.models.length) {
    return h("div", { class: "theos" }, h("div", { class: "legend-val quiet" }, "Theos: no data"));
  }
  const spend = typeof t.spend_today === "number" ? t.spend_today : null;
  const billable = t.models.filter((m) => m.cost > 0 && m.requests > 0).slice(0, 6);
  const anyRequests = t.models.some((m) => m.requests > 0);
  let status = null;
  if (t.exhausted) {
    const at = typeof t.refresh_at === "number"
      ? new Date(t.refresh_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;
    status = h("div", { class: "err" }, at ? `Used today's Theos — refreshes at ${at}` : "Used today's Theos");
  } else if (!anyRequests) {
    status = h("div", { class: "legend-val quiet" }, "No billable requests today");
  }
  return h("div", { class: "theos" },
    h("div", { class: "legend-row theos-head" }, h("span", { class: "legend-key" }, "Theos"),
      h("span", { class: "legend-val" }, spend === null ? "Not measured" : `${spend} Theos used today`)),
    status,
    billable.map((m) => [
      h("div", { class: "legend-row" }, h("span", { class: "legend-key wrap" }, m.name || m.id),
        h("span", { class: "legend-val" }, `${m.requests} request${m.requests === 1 ? "" : "s"}`)),
      h("div", { class: "row-sub theos-cost" }, `~${m.cost} Theos per request`),
    ]));
}

export function providerCard(p, now) {
  const c = p.client || {};
  let label, cls;
  if (p.loading || p.state === "loading") {
    label = "loading";
    cls = "";
  } else if (p.stale || p.state === "stale") {
    const age = (typeof p.age === "number") ? Math.round(p.age) : (p.fetched_at ? Math.max(0, Math.round(now - p.fetched_at)) : null);
    label = (age !== null && !isNaN(age)) ? `stale, ${age} s old` : "stale";
    cls = "";
  } else {
    [label, cls] = STATE[p.state] || [p.state, ""];
  }
  const pools = (p.pools || []).map((pool) => h("div", { class: "pool" }, ring(pool, now),
    h("div", { class: "legend" }, (p.pools.length > 1 || p.manual) ? h("div", { class: "pool-name" }, pool.name) : null,
      pool.windows.map((w, i) => windowRow(w, i, now)))));
  const foot = [p.source, p.model].filter(Boolean).join(" · ");
  return h("article", { class: "card" },
    h("div", { class: "provider-top" },
      h("div", {}, h("div", { class: "provider-name" }, p.name, p.manual ? h("span", { class: "tag", style: "margin-left: 8px;" }, "set by hand") : null), p.plan ? h("div", { class: "card-sub" }, p.plan) : null),
      h("span", { class: `tag ${cls}`.trim() }, label)),
    h("div", { class: "tags" }, chips(c, !!(p.memory))),
    p.id === "pi" ? theosBlock(p) : (pools.length ? h("div", { class: "pools" }, pools) : null),
    p.memory ? memoryBlock(p) : null,
    p.tokens ? tokenTable(p.tokens) : null,
    p.other_sessions ? h("div", { class: "row-sub" }, `Other sessions, last 5h: ${p.other_sessions.sessions ?? p.other_sessions.count ?? p.other_sessions.total ?? p.other_sessions} sessions, ${tokens(p.other_sessions.tokens ?? 0)} tokens`) : null,
    p.note ? h("div", { class: "note" }, p.note) : null,
    p.manual && p.source ? h("div", { class: "note" }, p.source) : null,
    p.error && p.state !== "live" && !p.loading && !p.stale && p.state !== "loading" && p.state !== "stale" ? h("div", { class: "err" }, p.error) : null,
    foot || p.fetched_at ? h("div", { class: "foot" }, foot,
      p.fetched_at ? [foot ? " · updated " : "Updated ", ticking("span", { class: "num" }, p.fetched_at, "age")] : null,
      typeof p.latency_ms === "number" && p.fetched_at ? ` · read in ${p.latency_ms >= 1000 ? (p.latency_ms / 1000).toFixed(1) + " s" : p.latency_ms + " ms"}` : null) : null,
    (c.evidence || []).length ? h("details", {}, h("summary", {}, "Why this was detected"), h("ul", {}, c.evidence.map((e) => h("li", {}, e)))) : null);
}

/* Upcoming resets across every provider, soonest first, for the overview hero. */
export function nextResets(providers, now) {
  const resets = [];
  for (const p of providers) {
    for (const pool of p.pools || []) {
      for (const w of pool.windows) {
        if (w.resets_at && w.resets_at > now && !w.disabled) {
          resets.push({ at: w.resets_at, label: `${p.name}${(p.pools.length > 1) ? ` ${pool.name}` : ""}, ${w.label.toLowerCase()}` });
        }
      }
    }
  }
  return resets.sort((a, b) => a.at - b.at);
}

/* Tokens per hour for the last 24 hours, per provider. */
export function spendChart(providers) {
  const series = providers.filter((p) => p.tokens && p.tokens.hourly);
  if (!series.length || !series.some((p) => p.tokens.hourly.some(Boolean))) {
    return h("div", { class: "empty" }, "No token usage recorded in the last 24 hours.");
  }
  const W = 640, H = 220, L = 44, B = 22, T = 10;
  const max = Math.max(...series.flatMap((p) => p.tokens.hourly)) || 1;
  const x = (i) => L + (i / 23) * (W - L - 8);
  const y = (v) => T + (1 - v / max) * (H - T - B);
  // providerColour imported from lib.js
  const grid = [0, 0.5, 1].map((f) => [
    s("line", { class: "chart-grid", x1: L, x2: W - 8, y1: y(max * f), y2: y(max * f) }),
    s("text", { class: "chart-axis", x: L - 6, y: y(max * f) + 3, "text-anchor": "end" }, tokens(Math.round(max * f))),
  ]);
  const ticks = [[0, "24h ago"], [11.5, "12h ago"], [23, "now"]].map(([i, label]) =>
    s("text", { class: "chart-axis", x: x(i), y: H - 6, "text-anchor": i === 0 ? "start" : i === 23 ? "end" : "middle" }, label));
  const lines = series.map((p) => {
    const pts = p.tokens.hourly.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    const colour = providerColour(p.id);
    return [
      s("polygon", { points: `${L},${y(0)} ${pts.join(" ")} ${W - 8},${y(0)}`, fill: colour, opacity: "0.12" }),
      s("polyline", { points: pts.join(" "), fill: "none", stroke: colour, "stroke-width": 2, "stroke-linejoin": "round" }),
    ];
  });
  const summary = series.map((p) => `${p.name} ${tokens(p.tokens.hourly.reduce((a, b) => a + b, 0))}`).join(", ");
  return [
    s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `New tokens per hour over 24 hours: ${summary}` }, grid, ticks, lines),
    h("div", { class: "chart-legend" }, series.map((p) => h("span", { class: "legend-key" },
      h("span", { class: "swatch", style: `background: ${providerColour(p.id)}` }), p.name, " ",
      h("b", {}, tokens(p.tokens.hourly.reduce((a, b) => a + b, 0)))))),
  ];
}

export function lowPools(p, now) {
  return (p.pools || [])
    .filter((pool) => (pool.windows || []).some((w) => usable(w, now) && w.left_pct <= 10))
    .map((pool) => pool.name);
}

export function reachesLimit(p, now) {
  return lowPools(p, now).length > 0;
}
