/* Premium chart primitives for the vanilla HUD: a notch gauge, a live line chart and a sankey tree.
   Ported from the bklit (React + d3 + motion) gauge-chart, live-line-chart and sankey-chart into
   plain SVG/CSS, with no dependencies. Every value is passed in; nothing is hardcoded. */
import { h, s, icon, tokens } from "./lib.js";

const NUM = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/* Blending needs real channel values, so a "var(--x)" colour is read from the root's computed
   style first. A one-colour gauge skips this and keeps the var(), which stays theme-live. */
function resolveColour(c) {
  const m = /^var\((--[\w-]+)\)$/.exec(String(c || "").trim());
  if (!m || typeof document === "undefined") return String(c || "").trim();
  return getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() || String(c);
}
function hexToRgb(hex) {
  const v = String(hex || "").trim();
  const rgb = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/.exec(v);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
  let c = v.replace("#", "");
  if (c.length === 3) c = c.split("").map((x) => x + x).join("");
  if (c.length !== 6) return [120, 130, 255];
  return [parseInt(c.slice(0, 2), 16), parseInt(c.slice(2, 4), 16), parseInt(c.slice(4, 6), 16)];
}
function mix(a, b, t) {
  if (String(a).trim() === String(b).trim()) return String(a).trim(); /* one colour: use as is */
  a = resolveColour(a); b = resolveColour(b);
  const [r1, g1, b1] = hexToRgb(a);
  const [r2, g2, b2] = hexToRgb(b);
  const f = Math.max(0, Math.min(1, t));
  return `rgb(${Math.round(r1 + (r2 - r1) * f)} ${Math.round(g1 + (g2 - g1) * f)} ${Math.round(b1 + (b2 - b1) * f)})`;
}
function smoothPath(pts) {
  if (!pts.length) return "";
  if (pts.length < 3) return `M${pts.map((p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" L")}`;
  let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
  const t = 0.17;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) * t;
    const c1y = p1[1] + (p2[1] - p0[1]) * t;
    const c2x = p2[0] - (p3[0] - p1[0]) * t;
    const c2y = p2[1] - (p3[1] - p1[1]) * t;
    const lo = Math.min(p1[1], p2[1]), hi = Math.max(p1[1], p2[1]);
    const cc1y = Math.min(hi, Math.max(lo, c1y));
    const cc2y = Math.min(hi, Math.max(lo, c2y));
    d += ` C${c1x.toFixed(1)} ${cc1y.toFixed(1)} ${c2x.toFixed(1)} ${cc2y.toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  return d;
}

let styled = false;
/* Keys whose chart has already played its draw-in animation, so a re-render never replays it. */
const animatedKeys = new Set();
function ensureStyles() {
  if (styled || typeof document === "undefined") return;
  styled = true;
  if (document.getElementById("subway-chart-style")) return;
  const css = `
  .gauge-wrap{display:flex;justify-content:center;align-items:center}
  .gauge-chart{width:100%;max-width:250px;height:auto;display:block}
  .gauge-chart .gauge-value{fill:var(--strong);font:700 30px var(--font-sans);font-variant-numeric:tabular-nums}
  .gauge-chart .gauge-caption{fill:var(--muted);font:600 11px var(--font-sans)}
  .live-chart{position:relative}
  .live-chart svg.chart{width:100%;height:auto;display:block;touch-action:none}
  .live-chart .chart-grid{stroke:var(--line-soft);stroke-width:1}
  .live-chart .chart-axis{fill:var(--muted);font:600 10px var(--font-mono)}
  .live-chart .live-nowline{stroke:var(--accent);stroke-width:1;stroke-dasharray:4 4;opacity:.35}
  .live-chart .live-badge text{fill:var(--strong);font:600 11px var(--font-mono)}
  .live-chart .live-pulse{transform-box:fill-box;transform-origin:center;opacity:0}
  .live-chart .live-tip.live-active .live-pulse{opacity:1;animation:subway-pulse 1.6s ease-out infinite}
  .live-chart .live-cross-line{stroke:var(--line);stroke-width:1}
  @keyframes subway-pulse{0%{transform:scale(1);opacity:.6}100%{transform:scale(3);opacity:0}}
  .sparkline{display:block;width:100%;height:auto;overflow:visible}
  .kbell{position:relative;display:inline-grid;place-items:center;width:36px;height:36px;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--strong);cursor:pointer}
  .kbell:hover{border-color:var(--accent-line);color:var(--accent)}
  .kbell svg{width:18px;height:18px}
  .kbell .ring{stroke:var(--accent)}
  .kbell.live .ring{stroke-dasharray:44;stroke-dashoffset:44;animation:kbell-ring 1.2s ease-out forwards}
  @keyframes kbell-ring{to{stroke-dashoffset:0}}
  .kbell .badge{position:absolute;top:-5px;right:-5px;min-width:16px;height:16px;padding:0 4px;border-radius:9px;background:var(--red);color:#fff;font:700 10px/16px var(--font-sans);text-align:center}
  /* Kokonut undo-button port (vanilla): pill with countdown stroke, swap/width motion, undo window. */
  .kundo{position:relative;display:inline-flex;vertical-align:middle;max-width:100%}
  .kundo-btn{position:relative;display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:0 16px;border:1.5px solid var(--line);border-radius:999px;background:var(--surface);color:var(--strong);font:500 13px/1 var(--font-sans);cursor:pointer;overflow:hidden;white-space:nowrap;user-select:none;box-sizing:border-box;outline:none;transition:background-color .15s ease-out,border-color .15s ease-out,color .15s ease-out,width .22s cubic-bezier(0.23,1,0.32,1)}
  .kundo-btn:hover{background:var(--raised);border-color:var(--line-soft)}
  .kundo-btn:active{background:var(--raised)}
  .kundo-btn:focus-visible{box-shadow:0 0 0 2px var(--bg),0 0 0 4px var(--accent-line)}
  .kundo-btn[data-phase="done"]{cursor:default;border-color:var(--line-soft);background:var(--raised);color:var(--muted)}
  .kundo-inner{display:inline-flex;align-items:center}
  .kundo-face{display:inline-flex;align-items:center;gap:8px;will-change:transform,opacity,filter}
  .kundo-face.kundo-swap{animation:kundo-in .22s cubic-bezier(0.23,1,0.32,1) both}
  .kundo-face .kicon{width:16px;height:16px;flex:none;display:inline-grid;place-items:center}
  .kundo-face .kicon svg{width:16px;height:16px;display:block}
  .kundo-muted{color:var(--muted)}
  .kundo-sep{width:1px;height:16px;flex:none;background:var(--line)}
  .kundo-count{position:relative;display:inline-flex;justify-content:center;align-items:center;height:20px;min-width:1ch;overflow:hidden;color:var(--muted);font-family:var(--font-mono);font-size:12px;font-variant-numeric:tabular-nums;line-height:20px}
  .kundo-count>span{display:block}
  .kundo-count>span.kundo-tick{animation:kundo-tick .2s cubic-bezier(0.23,1,0.32,1) both}
  .kundo-kbd{display:none;padding:1px 4px;border:1px solid var(--line);border-radius:4px;color:var(--muted);font:11px/1.2 var(--font-sans)}
  @media (min-width:640px){.kundo-kbd{display:inline-block}}
  .kundo-stroke{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;overflow:visible}
  .kundo-stroke path{fill:none;stroke:var(--strong);stroke-width:1.5;stroke-linecap:round;transition:opacity .15s ease-out}
  .kundo-sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
  @keyframes kundo-in{from{opacity:0;transform:translateY(8px);filter:blur(2px)}to{opacity:1;transform:translateY(0);filter:blur(0)}}
  @keyframes kundo-tick{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
  @media (prefers-reduced-motion: reduce){
    .kbell.live .ring{animation:none;stroke-dashoffset:0}
    .kundo-btn{transition:background-color .15s ease-out,border-color .15s ease-out,color .15s ease-out}
    .kundo-face.kundo-swap{animation:none}
    .kundo-count>span.kundo-tick{animation:none}
    .kundo-stroke path{transition:none}
  }
  .sankey{position:relative;height:min(62vh,580px);border:1px solid var(--line-soft);border-radius:var(--radius);overflow:auto;cursor:grab;background-color:var(--bg);background-image:linear-gradient(var(--line-soft) 1px,transparent 1px),linear-gradient(90deg,var(--line-soft) 1px,transparent 1px);background-size:100% 40px,40px 100%}
  .sankey.grabbing{cursor:grabbing}
  .sankey-zoom{position:relative}
  .sankey-stage{position:relative;transform-origin:0 0}
  .sankey-edges{position:absolute;inset:0;pointer-events:none;overflow:visible;z-index:0}
  .sankey-link{transition:opacity .12s ease}
  .sankey.tracing .sankey-link.dim{opacity:.08}
  .sankey-link.hi .sk-ribbon{opacity:.85}
  .sk-node{position:absolute;z-index:1;box-sizing:border-box;padding:7px 12px;display:flex;flex-direction:column;justify-content:center;gap:3px;overflow:hidden;border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--surface);box-shadow:0 10px 24px -18px #000;cursor:pointer;transition:opacity .12s ease,box-shadow .12s ease,border-color .12s ease}
  .sk-node .sk-label{font-size:12.5px;font-weight:700;color:var(--strong);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sk-node .sk-sub{font-family:var(--font-mono);font-size:10.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sankey.tracing .sk-node.dim{opacity:.26}
  .sk-node.hi{border-color:var(--accent-line);box-shadow:0 0 0 1px var(--accent-line),0 10px 24px -18px #000}
  .chart-legend-list{display:flex;flex-direction:column;gap:2px}
  .chart-legend-list.cols{display:grid;grid-template-columns:repeat(var(--cols),1fr);gap:2px 14px}
  .cl-item{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;column-gap:8px;padding:2px 0}
  .cl-label{min-width:0;font-size:12px;font-weight:700;color:var(--strong);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .cl-value{flex:none;margin-left:auto;font-family:var(--font-mono);font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
  .cl-bar{width:100%;height:4px;border-radius:999px;overflow:hidden;background:var(--line)}
  .cl-bar > i{display:block;height:100%;border-radius:999px}
  @media (prefers-reduced-motion: reduce){.live-chart .live-pulse{animation:none;opacity:0}}
  `;
  document.head.append(h("style", { id: "subway-chart-style" }, css));
}

export function notchGauge(opts = {}) {
  ensureStyles();
  const { value = 0, max = 100, label = "", caption = "", segments = 46, startDeg = 132, endDeg = 408,
    radius = 86, thickness = 17, size = 250, from = "#7c5cff", to = "#2f8fd1", track = "var(--line)",
    rings = null, format = (v) => String(Math.round(v)) } = opts;
  const cx = size / 2, cy = size / 2, sweep = endDeg - startDeg, step = sweep / segments;
  const svg = s("svg", { class: "gauge-chart", viewBox: `0 0 ${size} ${size}`, role: "img",
    "aria-label": `${label}: ${format(value)}${caption ? ` (${caption})` : ""}` });
  const g = s("g", {});
  /* One or more concentric rings, outer first. Each ring is a run of radial notches. */
  const list = Array.isArray(rings) && rings.length
    ? rings.map((r) => ({
        value: NUM(r.value), max: r.max == null ? 100 : NUM(r.max),
        radius: r.radius == null ? radius : r.radius, thickness: r.thickness == null ? thickness : r.thickness,
        from: r.from || from, to: r.to || to, track: r.track || track,
      }))
    : [{ value: NUM(value), max: NUM(max), radius, thickness, from, to, track }];
  for (const ring of list) {
    const frac = ring.max > 0 ? clamp(ring.value / ring.max, 0, 1) : 0;
    const spokeWidth = (Math.abs(sweep) * Math.PI / 180) * ring.radius / segments * 0.6;
    for (let i = 0; i < segments; i++) {
      const mid = (startDeg + (i + 0.5) * step - 90) * Math.PI / 180;
      const r1 = ring.radius - ring.thickness / 2, r2 = ring.radius + ring.thickness / 2;
      const active = (i + 0.5) / segments <= frac;
      g.append(s("line", {
        x1: (cx + r1 * Math.cos(mid)).toFixed(1), y1: (cy + r1 * Math.sin(mid)).toFixed(1),
        x2: (cx + r2 * Math.cos(mid)).toFixed(1), y2: (cy + r2 * Math.sin(mid)).toFixed(1),
        /* A CSS property (not the stroke attribute) so var() colours follow theme changes. */
        style: `stroke:${active ? mix(ring.from, ring.to, i / Math.max(1, segments - 1)) : ring.track}`,
        "stroke-width": spokeWidth.toFixed(1), "stroke-linecap": "round", opacity: active ? "1" : ".9",
      }));
    }
  }
  svg.append(g,
    s("text", { class: "gauge-value", x: cx, y: cy + 4, "text-anchor": "middle" }, format(value)),
    s("text", { class: "gauge-caption", x: cx, y: cy + 26, "text-anchor": "middle" }, caption || label));
  return h("div", { class: "gauge-wrap" }, svg);
}

export function liveLineChart(opts = {}) {
  ensureStyles();
  const { series = [], labels = [], height = 250, animateKey = "", tip = true, format = (v) => tokens(v) } = opts;
  const allowAnim = !(animateKey && animatedKeys.has(animateKey));
  const W = 680, H = height, L = 52, R = 20, T = 20, B = 30, iw = W - L - R, ih = H - T - B;
  const el = h("div", { class: "live-chart" });
  let current = series.filter((sr) => Array.isArray(sr.values) && sr.values.length);
  let hasAnimated = false;
  let svgEl = null; /* the svg is rebuilt on each draw; listeners live on el */
  function draw() {
    el.replaceChildren();
    const N = Math.max(0, ...current.map((sr) => sr.values.length));
    if (!N) { el.append(h("div", { class: "empty" }, "No token usage recorded in the last 24 hours.")); return; }
    const maxV = Math.max(1, ...current.flatMap((sr) => sr.values.map(NUM)));
    const xAt = (i) => L + (N === 1 ? iw / 2 : (i / (N - 1)) * iw);
    const yAt = (v) => T + (1 - NUM(v) / maxV) * ih;
    const svg = svgEl = s("svg", { class: "chart live", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img",
      "aria-label": `Tokens per hour: ${current.map((sr) => `${sr.name} ${format(sr.values.reduce((a, b) => a + NUM(b), 0))}`).join(", ")}` });
    const defs = s("defs", {});
    current.forEach((sr, k) => { defs.append(
      s("linearGradient", { id: `ll-line-${k}`, x1: "0", x2: "0", y1: "0", y2: "1" },
        s("stop", { offset: "0%", "stop-color": sr.color, "stop-opacity": "1" }),
        s("stop", { offset: "100%", "stop-color": sr.color, "stop-opacity": ".55" })),
      s("linearGradient", { id: `ll-area-${k}`, x1: "0", x2: "0", y1: "0", y2: "1" },
        s("stop", { offset: "0%", "stop-color": sr.color, "stop-opacity": ".28" }),
        s("stop", { offset: "100%", "stop-color": sr.color, "stop-opacity": "0" }))); });
    svg.append(defs);
    for (const f of [0, 0.5, 1]) { const y = T + (1 - f) * ih;
      svg.append(s("line", { class: "chart-grid", x1: L, x2: W - R, y1: y, y2: y }));
      svg.append(s("text", { class: "chart-axis", x: L - 8, y: y + 3, "text-anchor": "end" }, format(Math.round(maxV * f)))); }
    [0, Math.floor((N - 1) / 2), N - 1].forEach((i, k) => { if (i < 0) return;
      svg.append(s("text", { class: "chart-axis", x: xAt(i), y: H - 9,
        "text-anchor": k === 0 ? "start" : (k === 2 ? "end" : "middle") }, labels[i] || "")); });
    const primary = current[0];
    const lastY = yAt(primary.values[primary.values.length - 1]);
    /* "now" is only now when the chart is scrolled to the live edge. */
    if (tip) {
      svg.append(s("line", { class: "live-nowline", x1: L, x2: W - R, y1: lastY, y2: lastY }));
      svg.append(s("text", { class: "chart-axis", x: W - R, y: lastY - 6, "text-anchor": "end" }, format(primary.values[primary.values.length - 1])));
    }
    const plot = s("g", { class: "live-plot" });
    current.forEach((sr, k) => {
      const pts = sr.values.map((v, i) => [xAt(i), yAt(v)]);
      const d = smoothPath(pts);
      plot.append(s("path", { class: "live-area", d: `${d} L${xAt(N - 1).toFixed(1)} ${(T + ih).toFixed(1)} L${xAt(0).toFixed(1)} ${(T + ih).toFixed(1)} Z`, fill: `url(#ll-area-${k})` }));
      plot.append(s("path", { class: "live-line", d, fill: "none", stroke: `url(#ll-line-${k})`, "stroke-width": "2.4", "stroke-linecap": "round", "stroke-linejoin": "round" })); });
    svg.append(plot);
    if (tip) {
    const tipX = xAt(N - 1), tipY = yAt(primary.values[primary.values.length - 1]);
    const tip = s("g", { class: "live-tip" },
      s("circle", { class: "live-pulse", cx: tipX, cy: tipY, r: "5", fill: "none", stroke: primary.color, "stroke-width": "1.5" }),
      s("circle", { cx: tipX, cy: tipY, r: "5", fill: primary.color, stroke: "var(--surface)", "stroke-width": "2" }));
    svg.append(tip);
    const bw = format(primary.values[primary.values.length - 1]).length * 7.4 + 18;
    const bx = Math.max(L, Math.min(W - R - bw, tipX - bw / 2));
    svg.append(s("g", { class: "live-badge", transform: `translate(${bx.toFixed(1)},${(tipY - 34).toFixed(1)})` },
      s("rect", { width: bw, height: "24", rx: "7", fill: "var(--popover, var(--raised))", stroke: "var(--line)" }),
      s("text", { x: bw / 2, y: "16", "text-anchor": "middle" }, format(primary.values[primary.values.length - 1]))));
    }
    const cross = s("g", { class: "live-cross", opacity: "0" },
      s("line", { class: "live-cross-line", x1: "0", x2: "0", y1: T, y2: T + ih }), s("g", { class: "live-cross-dots" }));
    svg.append(cross); el.append(svg);
    /* Crosshair listeners live on the container: draw() rebuilds the svg, and
       listeners hung on the svg would die with it after the first update(). */
    el.onpointermove = (ev) => {
      const svgNow = svgEl, crossNow = svgNow && svgNow.querySelector(".live-cross"), tipNow = svgNow && svgNow.querySelector(".live-tip");
      if (!svgNow || !crossNow || !tipNow) return;
      const dots = crossNow.querySelector(".live-cross-dots"), line = crossNow.querySelector(".live-cross-line");
      const rect = svgNow.getBoundingClientRect();
      const px = ((ev.clientX - rect.left) / rect.width) * W;
      const i = clamp(Math.round(((px - L) / iw) * (N - 1)), 0, N - 1), x = xAt(i);
      line.setAttribute("x1", x); line.setAttribute("x2", x);
      dots.replaceChildren(...current.map((sr) => s("circle", { cx: x, cy: yAt(sr.values[i] ?? 0), r: "4", fill: sr.color, stroke: "var(--surface)", "stroke-width": "2" })));
      crossNow.setAttribute("opacity", "1"); tipNow.setAttribute("opacity", ".25"); tipNow.classList.add("live-active"); };
    el.onpointerleave = () => {
      const svgNow = svgEl, crossNow = svgNow && svgNow.querySelector(".live-cross"), tipNow = svgNow && svgNow.querySelector(".live-tip");
      if (!svgNow || !crossNow || !tipNow) return;
      crossNow.setAttribute("opacity", "0"); tipNow.setAttribute("opacity", "1"); tipNow.classList.remove("live-active"); };
    if (!hasAnimated) {
      hasAnimated = true;
      if (animateKey) animatedKeys.add(animateKey);
      if (allowAnim) {
        svg.querySelectorAll(".live-line").forEach((pth, k) => {
          let len = 0; try { len = pth.getTotalLength(); } catch (e) { len = 0; }
          if (!len) return;
          pth.style.strokeDasharray = `${len}`; pth.style.strokeDashoffset = `${len}`;
          requestAnimationFrame(() => { pth.style.transition = `stroke-dashoffset .9s ease-out ${k * 0.08}s`; pth.style.strokeDashoffset = "0"; });
        });
      }
    }
  }
  el.update = (next) => { current = (next || []).filter((sr) => Array.isArray(sr.values) && sr.values.length); draw(); };
  draw();
  return el;
}

export function sparkline(values, colour, opts = {}) {
  ensureStyles();
  const { w = 120, h = 30 } = opts;
  const vals = (values || []).map(NUM);
  if (vals.length < 2) return h("span", { class: "sparkline-empty" }, "\u2014");
  const max = Math.max(...vals) || 1, min = Math.min(...vals), span = max - min || 1;
  const pts = vals.map((v, i) => [(i / (vals.length - 1)) * w, h - 3 - ((v - min) / span) * (h - 8)]);
  const d = smoothPath(pts);
  return s("svg", { class: "sparkline", viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none", "aria-hidden": "true" },
    s("path", { d: `${d} L${w} ${h} L0 ${h} Z`, fill: colour, opacity: ".14", stroke: "none" }),
    s("path", { d, fill: "none", stroke: colour, "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" }));
}

/* Zoom + scroll position per chart key, kept across rebuilds. */
const sankeyState = new Map();

export function sankeyChart(opts = {}) {
  ensureStyles();
  const {
    key = "default",
    nodes = [],
    links = [],
    colour = () => "var(--accent)",
    readout = null,
    onClick = () => {},
    nodeWidth = 264,
    nodeHeight = 46,
    colGap = 120,
    rowGap = 14,
    pad = 24,
  } = opts;

  /* 1. Index the graph. Node ids are keyed as strings; children keep link order. */
  const byId = new Map();
  nodes.forEach((n) => { if (n !== null && n !== undefined && n.id !== undefined) byId.set(String(n.id), n); });
  const children = new Map();
  const parentOf = new Map();
  links.forEach((l) => {
    if (!byId.has(String(l.source)) || !byId.has(String(l.target))) return;
    const src = String(l.source), tgt = String(l.target);
    if (!children.has(src)) children.set(src, []);
    children.get(src).push(tgt);
    if (!parentOf.has(tgt)) parentOf.set(tgt, src);
  });

  /* 2. Depth: explicit node.depth wins, parentless nodes sit at 0, children follow parents. */
  const depths = new Map();
  function depthOf(id, guard) {
    if (depths.has(id)) return depths.get(id);
    const g = guard || new Set();
    if (g.has(id)) return 0;
    g.add(id);
    const node = byId.get(id);
    let d;
    if (node && typeof node.depth === "number" && Number.isFinite(node.depth)) d = node.depth;
    else {
      const p = parentOf.get(id);
      d = p === undefined ? 0 : depthOf(p, g) + 1;
    }
    depths.set(id, d);
    return d;
  }

  /* 3-4. Tidy vertical layout, post-order: leaves take rows from a cursor, parents centre on them. */
  const yOf = new Map();
  const placed = new Set();
  let cursor = pad;
  function place(id, guard) {
    if (placed.has(id) || !byId.has(id)) return;
    const g = guard || new Set();
    if (g.has(id)) { placed.add(id); return; }
    g.add(id);
    const kids = (children.get(id) || []).filter((k) => byId.has(k));
    if (!kids.length) { yOf.set(id, cursor); cursor += nodeHeight + rowGap; }
    else {
      kids.forEach((k) => place(k, g));
      const ys = kids.map((k) => yOf.get(k)).filter((y) => Number.isFinite(y));
      if (!ys.length) { yOf.set(id, cursor); cursor += nodeHeight + rowGap; }
      else yOf.set(id, (ys[0] + ys[ys.length - 1]) / 2);
    }
    placed.add(id);
  }
  nodes.forEach((n) => { if (n !== null && n !== undefined && n.id !== undefined && !parentOf.has(String(n.id))) place(String(n.id)); });
  nodes.forEach((n) => { if (n !== null && n !== undefined && n.id !== undefined) place(String(n.id)); });

  let maxDepth = 0;
  nodes.forEach((n) => { if (n !== null && n !== undefined && n.id !== undefined) maxDepth = Math.max(maxDepth, depthOf(String(n.id))); });

  /* 5. Stage extents. */
  const stageW = pad * 2 + (maxDepth + 1) * (nodeWidth + colGap) - colGap;
  const stageH = Math.max(220, cursor + pad);
  const xOf = (id) => pad + depthOf(id) * (nodeWidth + colGap);
  const f1 = (v) => v.toFixed(1);

  /* 6. Ribbons: one SVG, each link a <g class="sankey-link" data-target> wrapping ribbon + centre line. */
  const root = h("div", { class: "sankey" });
  const zoomBox = h("div", { class: "sankey-zoom" });
  const stage = h("div", { class: "sankey-stage", style: `width:${f1(stageW)}px;height:${f1(stageH)}px` });
  const edges = s("svg", { class: "sankey-edges", viewBox: `0 0 ${f1(stageW)} ${f1(stageH)}`, width: f1(stageW), height: f1(stageH) });
  const linkEls = [];
  links.forEach((l) => {
    const p = byId.get(String(l.source)), c = byId.get(String(l.target));
    if (!p || !c || !placed.has(String(l.source)) || !placed.has(String(l.target))) return;
    const x1 = xOf(String(l.source)) + nodeWidth, y1 = yOf.get(String(l.source)) + nodeHeight / 2;
    const x2 = xOf(String(l.target)), y2 = yOf.get(String(l.target)) + nodeHeight / 2;
    const mx = (x1 + x2) / 2;
    const col = colour(c) || "var(--accent)";
    const ribbon = `M${f1(x1)} ${f1(y1 - 4)} C${f1(mx)} ${f1(y1 - 4)} ${f1(mx)} ${f1(y2 - 4)} ${f1(x2)} ${f1(y2 - 4)} L${f1(x2)} ${f1(y2 + 4)} C${f1(mx)} ${f1(y2 + 4)} ${f1(mx)} ${f1(y1 + 4)} ${f1(x1)} ${f1(y1 + 4)} Z`;
    const centre = `M${f1(x1)} ${f1(y1)} C${f1(mx)} ${f1(y1)} ${f1(mx)} ${f1(y2)} ${f1(x2)} ${f1(y2)}`;
    const g = s("g", { class: "sankey-link", "data-target": String(l.target) },
      s("path", { class: "sk-ribbon", d: ribbon, fill: col, opacity: ".45", stroke: "none" }),
      s("path", { class: "sk-line", d: centre, fill: "none", stroke: col, "stroke-width": "2" }));
    linkEls.push(g);
    edges.append(g);
  });

  /* 7-8. Nodes: absolute cards on the stage, click + hover trace to ancestors and descendants. */
  const nodeEls = [];
  nodes.forEach((n) => {
    if (n === null || n === undefined || n.id === undefined) return;
    const id = String(n.id);
    const y = yOf.has(id) ? yOf.get(id) : pad;
    const el = h("div", {
      class: "sk-node", "data-id": id,
      style: `left:${f1(xOf(id))}px;top:${f1(y)}px;width:${nodeWidth}px;height:${nodeHeight}px`,
    },
      h("div", { class: "sk-label" }, n.label ?? id),
      h("div", { class: "sk-sub" }, n.sub));
    el.addEventListener("click", () => onClick(id));
    el.addEventListener("mouseenter", () => trace(id));
    el.addEventListener("mouseleave", untrace);
    nodeEls.push(el);
    stage.append(el);
  });

  function trace(id) {
    const up = new Set([id]);
    for (let p = parentOf.get(id); p !== undefined && !up.has(p); p = parentOf.get(p)) up.add(p);
    const down = new Set([id]);
    const stack = [id];
    while (stack.length) {
      const cur = stack.pop();
      (children.get(cur) || []).forEach((k) => { if (byId.has(k) && !down.has(k)) { down.add(k); stack.push(k); } });
    }
    const lit = new Set([...up, ...down]);
    linkEls.forEach((g) => {
      const on = lit.has(g.getAttribute("data-target"));
      g.classList.toggle("hi", on);
      g.classList.toggle("dim", !on);
    });
    nodeEls.forEach((el) => {
      const on = lit.has(el.getAttribute("data-id"));
      el.classList.toggle("hi", on);
      el.classList.toggle("dim", !on);
    });
    root.classList.add("tracing");
    if (!readout) return;
    const chain = [];
    const seen = new Set();
    for (let c = id; c !== undefined && !seen.has(c); c = parentOf.get(c)) { seen.add(c); chain.push(c); }
    chain.reverse();
    const kids = [h("span", { class: "gr-k" }, "came from ")];
    chain.forEach((cid, i) => {
      if (i) kids.push(" \u2192 ");
      const lab = String((byId.get(cid) || {}).label ?? cid);
      kids.push(lab.length > 28 ? `${lab.slice(0, 28)}\u2026` : lab);
    });
    kids.push(h("span", { class: "dim" }, ` \u00b7 ${down.size - 1} downstream`));
    readout.replaceChildren(...kids);
  }
  function untrace() {
    root.classList.remove("tracing");
    linkEls.forEach((g) => g.classList.remove("hi", "dim"));
    nodeEls.forEach((el) => el.classList.remove("hi", "dim"));
    if (readout) readout.replaceChildren();
  }

  /* 9-10. Zoom + pan + scroll, persisted per key across rebuilds. */
  let zoom = 1;
  const persist = () => sankeyState.set(key, { zoom, x: root.scrollLeft, y: root.scrollTop });
  function applyZoom(z) {
    zoom = clamp(z, 0.15, 3);
    stage.style.transform = `scale(${zoom})`;
    zoomBox.style.width = `${f1(stageW * zoom)}px`;
    zoomBox.style.height = `${f1(stageH * zoom)}px`;
  }
  root._setZoom = (z) => {
    const cw = root.clientWidth, ch = root.clientHeight;
    const sx = zoom ? (root.scrollLeft + cw / 2) / zoom : stageW / 2;
    const sy = zoom ? (root.scrollTop + ch / 2) / zoom : stageH / 2;
    applyZoom(NUM(z) || 1);
    if (cw) root.scrollLeft = Math.max(0, sx * zoom - cw / 2);
    if (ch) root.scrollTop = Math.max(0, sy * zoom - ch / 2);
    persist();
    root.dispatchEvent(new CustomEvent("zoomchange", { detail: zoom }));
  };
  root._fit = () => {
    const cw = root.clientWidth, ch = root.clientHeight;
    applyZoom(cw && ch ? Math.min(cw / stageW, ch / stageH) : 1);
    if (cw) root.scrollLeft = Math.max(0, (stageW * zoom - cw) / 2);
    if (ch) root.scrollTop = Math.max(0, (stageH * zoom - ch) / 2);
    persist();
    root.dispatchEvent(new CustomEvent("zoomchange", { detail: zoom }));
  };
  root._nodes = nodes.length;
  root._links = links.length;
  root._stageW = stageW;
  root._stageH = stageH;
  Object.defineProperty(root, "_zoom", { get: () => zoom });

  root.addEventListener("wheel", (ev) => {
    if (!ev.ctrlKey && !ev.metaKey) return;
    ev.preventDefault();
    root._setZoom(zoom * (ev.deltaY < 0 ? 1.12 : 1 / 1.12));
  }, { passive: false });

  let drag = null;
  root.addEventListener("pointerdown", (ev) => {
    if (ev.target && ev.target.closest && ev.target.closest(".sk-node")) return;
    drag = { x: ev.clientX, y: ev.clientY, sl: root.scrollLeft, st: root.scrollTop };
    root.classList.add("grabbing");
    try { root.setPointerCapture(ev.pointerId); } catch (e) { /* capture is best effort */ }
  });
  root.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    root.scrollLeft = drag.sl - (ev.clientX - drag.x);
    root.scrollTop = drag.st - (ev.clientY - drag.y);
  });
  const endDrag = () => { drag = null; root.classList.remove("grabbing"); };
  root.addEventListener("pointerup", endDrag);
  root.addEventListener("pointercancel", endDrag);
  root.addEventListener("scroll", persist, { passive: true });

  stage.append(edges);
  zoomBox.append(stage);
  root.append(zoomBox);

  const saved = sankeyState.get(key);
  applyZoom(saved && Number.isFinite(saved.zoom) ? saved.zoom : 1);
  queueMicrotask(() => requestAnimationFrame(() => {
    if (saved) {
      root.scrollLeft = NUM(saved.x);
      root.scrollTop = NUM(saved.y);
    }
  }));

  return root;
}

/* A compact key: one row per item — marker, bold label, formatted value, optional progress bar. */
export function legend(items, opts = {}) {
  ensureStyles();
  const { columns = 1, showProgress = true, format = (v) => String(v) } = opts;
  const list = h("div", { class: columns > 1 ? "chart-legend-list cols" : "chart-legend-list", style: columns > 1 ? `--cols:${columns}` : "" });
  (items || []).forEach((it) => {
    if (it === null || it === undefined) return;
    const colour = it.color || "var(--accent)";
    const hasValue = it.value !== null && it.value !== undefined;
    const item = h("div", { class: "cl-item", title: it.note || undefined },
      h("span", { class: "cl-label" }, it.label ?? ""),
      h("span", { class: "cl-value" }, hasValue ? format(it.value) : "\u2014"));
    if (showProgress && it.max != null) {
      const frac = it.max > 0 && hasValue ? clamp(NUM(it.value) / NUM(it.max), 0, 1) : 0;
      item.append(h("div", { class: "cl-bar", style: "grid-column:1 / -1;margin-top:4px" },
        h("i", { style: `width:${(frac * 100).toFixed(1)}%;background:${colour}` })));
    }
    list.append(item);
  });
  return list;
}
/* ---- Kokonutui-derived controls: switch, bell, undo (vanilla ports) ---- */
const BELL_PATH = "M168 88a40 40 0 1 0-80 0c0 56-24 72-24 72h128s-24-16-24-72M96 208a24 24 0 0 0 48 0";
const UNDO_PATH = "M224,128a96,96,0,0,1-94.71,96H128A95.38,95.38,0,0,1,62.1,197.8a8,8,0,0,1,11-11.63A80,80,0,1,0,71.43,71.39a3.07,3.07,0,0,1-.26.25L44.59,96H72a8,8,0,0,1,0,16H24a8,8,0,0,1-8-8V56a8,8,0,0,1,16,0V85.8L60.25,60A96,96,0,0,1,224,128Z";

function iconPath(d) {
  return s("svg", { viewBox: "0 0 256 256", fill: "none", stroke: "currentColor", "stroke-width": "18", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" }, s("path", { d }));
}

export function bellButton(opts = {}) {
  ensureStyles();
  const { count = 0, active = false, title = "Notifications", onClick = () => {} } = opts;
  const el = h("button", { class: `kbell${active ? " live" : ""}`, type: "button", title, "aria-label": `${title}${count ? `, ${count} pending` : ""}` },
    s("svg", { viewBox: "0 0 256 256", fill: "none", "aria-hidden": "true" },
      s("path", { class: "ring", d: "M40 128a88 88 0 1 1 4 26", "stroke-width": "10", "stroke-linecap": "round" }),
      s("path", { d: BELL_PATH, stroke: "currentColor", "stroke-width": "16", "stroke-linecap": "round", "stroke-linejoin": "round" })),
    count > 0 ? h("span", { class: "badge" }, count > 99 ? "99+" : String(count)) : null);
  el.addEventListener("click", () => { el.classList.remove("live"); void el.offsetWidth; if (active) el.classList.add("live"); onClick(); });
  return el;
}

const KUNDO_EASE = "cubic-bezier(0.23,1,0.32,1)";
const KUNDO_STROKE = 1.5;
function kundoPillPath(width, height) {
  const s = KUNDO_STROKE / 2;
  const r = Math.max(0, height / 2 - s);
  const left = s + r;
  const right = Math.max(left, width - s - r);
  return [
    `M ${width / 2} ${s}`,
    `H ${right}`,
    `A ${r} ${r} 0 0 1 ${right} ${height - s}`,
    `H ${left}`,
    `A ${r} ${r} 0 0 1 ${left} ${s}`,
    "Z",
  ].join(" ");
}
function kundoEditable(target) {
  return target instanceof HTMLElement
    && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
}
function kundoIcon(name) {
  if (name === false || name == null) return null;
  if (typeof name === "string" && !/^M[\d.,\s-]/.test(name)) {
    // HUD icon name (archive/check/refresh/...) or known shortcuts.
    if (name === "undo") return iconPath(UNDO_PATH);
    try { return icon(name); } catch (_) { /* fall through */ }
  }
  // Raw SVG path string.
  return iconPath(typeof name === "string" ? name : UNDO_PATH);
}

/* Port of KokonutUI undo-button (vanilla JS/CSS). Keeps backward compat: onConfirm maps to onCommit. */
export function undoButton(opts = {}) {
  ensureStyles();
  const label = opts.label == null ? "Archive" : String(opts.label);
  const doneLabel = opts.doneLabel == null ? (label.endsWith("e") ? `${label}d` : `${label}ed`) : String(opts.doneLabel);
  const iconName = Object.prototype.hasOwnProperty.call(opts, "icon") ? opts.icon : "archive";
  const duration = Math.max(0, Number(opts.duration) || 5000);
  const resetAfter = opts.resetAfter === null ? null : Math.max(0, Number(opts.resetAfter ?? 2400));
  const callbacks = {
    onAction: typeof opts.onAction === "function" ? opts.onAction : () => {},
    onCommit: typeof opts.onCommit === "function" ? opts.onCommit : (typeof opts.onConfirm === "function" ? opts.onConfirm : () => {}),
    onUndo: typeof opts.onUndo === "function" ? opts.onUndo : () => {},
  };
  const reduceMotion = typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || "");

  let phase = "idle";
  let seconds = Math.ceil(duration / 1000);
  let width = null;
  let hovered = false;
  let elapsed = 0;
  let remaining = 1;
  let raf = 0;
  let resetTimer = 0;
  let pathLen = 0;
  let dead = false;

  const inner = h("span", { class: "kundo-inner" });
  const btn = h("button", { class: "kundo-btn", type: "button", "data-phase": "idle" });
  btn.append(inner);
  const path = s("path", { d: "", "aria-hidden": "true" });
  const strokeSvg = s("svg", { class: "kundo-stroke", "aria-hidden": "true", focusable: "false" }, path);
  const sr = h("span", { class: "kundo-sr", "aria-live": "polite", "aria-atomic": "true" });
  const wrap = h("span", { class: `kundo${opts.className ? ` ${opts.className}` : ""}` }, btn, strokeSvg, sr);

  function setHovered(ev, val) {
    if (ev && ev.pointerType && ev.pointerType !== "mouse") return;
    hovered = !!val;
  }
  function paint() {
    if (!path.isConnected) return;
    try {
      if (!pathLen) pathLen = path.getTotalLength() || 0;
    } catch (_) { pathLen = 0; }
    if (!pathLen) return;
    path.style.strokeDasharray = `${pathLen * remaining} ${pathLen}`;
    path.style.strokeDashoffset = "0";
  }
  function syncPath() {
    if (!btn.isConnected) return;
    const w = btn.offsetWidth;
    const ht = btn.offsetHeight;
    if (w > 0 && ht > 0) path.setAttribute("d", kundoPillPath(w, ht));
    pathLen = 0;
    paint();
  }
  function syncWidth() {
    if (!inner.isConnected) return;
    const w = Math.ceil(inner.offsetWidth + 34); // inner padding + border budget
    if (w > 0 && w !== width) {
      width = w;
      btn.style.width = `${w}px`;
    }
  }
  function ariaLabel() {
    if (phase === "idle") return label;
    if (phase === "pending") return `${doneLabel}. Undo, ${seconds} second${seconds === 1 ? "" : "s"} left`;
    return doneLabel;
  }
  function renderCountdown(val, tick) {
    const box = h("span", { class: "kundo-count", "aria-hidden": "true" });
    const n = h("span", { class: tick && !reduceMotion ? "kundo-tick" : "" }, String(val));
    box.append(n);
    return box;
  }
  function renderFace() {
    const face = h("span", { class: `kundo-face${reduceMotion ? "" : " kundo-swap"}` });
    if (phase === "idle") {
      const ic = kundoIcon(iconName);
      if (ic) face.append(h("span", { class: "kicon", "aria-hidden": "true" }, ic));
      face.append(document.createTextNode(label));
    } else if (phase === "pending") {
      face.append(h("span", { class: "kundo-muted" }, doneLabel));
      face.append(h("span", { class: "kundo-sep", "aria-hidden": "true" }));
      face.append(h("span", { class: "kicon", "aria-hidden": "true" }, iconPath(UNDO_PATH)));
      face.append(document.createTextNode("Undo"));
      face.append(renderCountdown(seconds, true));
      face.append(h("kbd", { class: "kundo-kbd" }, isMac ? "⌘Z" : "Ctrl Z"));
    } else {
      face.append(h("span", { class: "kicon", "aria-hidden": "true" }, iconPath("check")));
      face.append(document.createTextNode(doneLabel));
    }
    return face;
  }
  function updateSr() {
    if (phase === "pending") sr.textContent = `${doneLabel}. Press the button or ${isMac ? "Command" : "Control"} Z to undo.`;
    else if (phase === "done") sr.textContent = doneLabel;
    else sr.textContent = "";
  }
  function setPhase(next) {
    phase = next;
    btn.setAttribute("data-phase", phase);
    btn.setAttribute("aria-label", ariaLabel());
    btn.setAttribute("aria-disabled", phase === "done" ? "true" : "false");
    inner.replaceChildren(renderFace());
    path.style.opacity = phase === "pending" ? "1" : "0";
    if (phase !== "pending") {
      path.style.transitionDuration = phase === "done" ? "0.2s" : "0.15s";
      path.style.strokeDasharray = "";
    } else {
      path.style.transitionDuration = "0.15s";
    }
    updateSr();
    // Width follows content; sync after layout.
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => { syncWidth(); syncPath(); });
    else { syncWidth(); syncPath(); }
  }
  function stopRaf() { if (raf) { cancelAnimationFrame(raf); raf = 0; } }
  function clearReset() { if (resetTimer) { clearTimeout(resetTimer); resetTimer = 0; } }
  function commit() {
    stopRaf();
    setPhase("done");
    try { callbacks.onCommit(); } catch (_) {}
    if (resetAfter !== null) {
      clearReset();
      resetTimer = setTimeout(() => { if (!dead) setPhase("idle"); }, resetAfter);
    }
  }
  function undo() {
    if (phase !== "pending") return;
    stopRaf();
    setPhase("idle");
    try { callbacks.onUndo(); } catch (_) {}
  }
  function onKeyDown(ev) {
    if (phase !== "pending" || dead) return;
    const mod = ev.metaKey || ev.ctrlKey;
    if (mod && !ev.shiftKey && String(ev.key || "").toLowerCase() === "z" && !kundoEditable(ev.target)) {
      ev.preventDefault();
      undo();
    }
  }
  function startPending() {
    clearReset();
    elapsed = 0;
    remaining = 1;
    seconds = Math.ceil(duration / 1000);
    setPhase("pending");
    try { callbacks.onAction(); } catch (_) {}
    paint();
    let last = typeof performance !== "undefined" ? performance.now() : Date.now();
    const tick = (now) => {
      if (dead || phase !== "pending") return;
      if (!(hovered || (typeof document !== "undefined" && document.hidden))) {
        elapsed += now - last;
      }
      last = now;
      const left = Math.max(0, duration - elapsed);
      remaining = duration > 0 ? left / duration : 0;
      const nextSec = Math.ceil(left / 1000);
      if (nextSec !== seconds) {
        seconds = nextSec;
        btn.setAttribute("aria-label", ariaLabel());
        // Update just the countdown number to get the tick animation.
        const count = inner.querySelector(".kundo-count");
        if (count) count.replaceChildren(h("span", { class: reduceMotion ? "" : "kundo-tick" }, String(seconds)));
      }
      paint();
      if (left === 0) { commit(); return; }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  }

  btn.addEventListener("click", () => {
    if (dead) return;
    if (phase === "idle") startPending();
    else if (phase === "pending") undo();
  });
  btn.addEventListener("pointerenter", (ev) => setHovered(ev, true));
  btn.addEventListener("pointerleave", (ev) => setHovered(ev, false));
  if (typeof window !== "undefined") window.addEventListener("keydown", onKeyDown);

  if (typeof ResizeObserver !== "undefined") {
    const roBtn = new ResizeObserver(() => syncPath());
    roBtn.observe(btn);
    const roInner = new ResizeObserver(() => syncWidth());
    roInner.observe(inner);
    wrap._ro = [roBtn, roInner];
  }

  wrap._stop = () => {
    dead = true;
    stopRaf();
    clearReset();
    if (typeof window !== "undefined") window.removeEventListener("keydown", onKeyDown);
    if (wrap._ro) for (const r of wrap._ro) try { r.disconnect(); } catch (_) {}
  };
  // Public escape hatch for harness/manual control (not part of old API).
  wrap._undo = undo;
  wrap._commit = commit;

  // Initial render.
  setPhase("idle");
  // Ensure path/width sync once in DOM.
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => { syncWidth(); syncPath(); });

  return wrap;
}


/* ---- Horizontal bar chart ----
   Ported from the bklit bar-chart (React + visx + motion), horizontal orientation, as plain DOM:
   round-capped bars, a dashed grid, bars that grow in with a stagger across 40% of the duration,
   and hovering one bar fades the rest to 30%. Rows are buttons: clicking one selects it, and the
   caller's detail node opens inline under it.

   The grow plays only when `animateKey` changes (metric, filter, model set), never on the HUD's
   routine re-render, which rebuilds this DOM every poll. */
const barChartSeen = new Map(); /* chart id -> last animateKey */
const barScroll = new Map();
const BAR_MS = 1100;

export function barChart(opts = {}) {
  const {
    id = "bar-chart",
    orientation = "horizontal",
    rows = [],                 /* [{ id, label, value|null, display, color, badges:[{text,tone}], sub }] */
    max = 0,                   /* axis maximum; derived from the data when 0 */
    ticks = null,              /* explicit tick values; derived from max when null */
    format = (v) => String(v),
    animateKey = "",
    selectedId = null,
    onSelect = null,
    detail = null,             /* (row) => Node, rendered under the selected row */
    tooltip = null,            /* (row) => Node | null */
    emptyText = "No data.",
    ariaLabel = "Bar chart",
  } = opts;

  const values = rows.map((r) => (r.value == null ? null : NUM(r.value)));
  const top = max > 0 ? max : Math.max(0, ...values.filter((v) => v != null));
  const axisMax = top > 0 ? top : 1;
  const tickList = Array.isArray(ticks) && ticks.length ? ticks : niceTicks(axisMax);
  const reduce = typeof window !== "undefined" && window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const animate = !reduce && barChartSeen.get(id) !== animateKey;
  barChartSeen.set(id, animateKey);

  if (orientation === "vertical") {
    const wrap = h("div", { class: "bc bc-v", role: "group", "aria-label": ariaLabel });
    if (!rows.length) {
      wrap.appendChild(h("div", { class: "bc-empty" }, emptyText));
      return wrap;
    }

    const tip = h("div", { class: "bc-tip", role: "tooltip", hidden: true });

    const axis = h("div", { class: "bc-v-axis", "aria-hidden": "true" },
      tickList.map((t) => h("span", {
        class: "bc-tick",
        style: `bottom:${clamp((t / axisMax) * 100, 0, 100)}%`,
      }, format(t))));

    const plot = h("div", { class: "bc-v-plot" },
      h("div", { class: "bc-vgrid-layer" },
        tickList.map((t) => h("span", {
          class: "bc-vgrid",
          style: `bottom:${clamp((t / axisMax) * 100, 0, 100)}%`,
          "aria-hidden": "true",
        }))));

    rows.forEach((r, i) => {
      const v = values[i];
      const pct = v == null ? 0 : clamp((v / axisMax) * 100, 0, 100);
      const isSel = selectedId != null && r.id === selectedId;
      const hasBadges = Array.isArray(r.badges) && r.badges.length > 0;
      const hasBad = hasBadges && r.badges.some((b) => b.tone === "bad");
      const mark = hasBadges ? h("span", {
        class: `bc-mark ${hasBad ? "bad" : "scarce"}`,
        style: `bottom:calc(${pct}% + 18px);`,
      }) : null;

      const valEl = h("span", {
        class: "bc-val",
        style: `bottom:calc(${pct}% + 4px);`,
      }, v == null ? "—" : (r.display ?? format(v)));

      const delayMs = rows.length ? Math.round((i * 440) / rows.length) : 0;
      let barStyle = `height:${pct}%;background:${r.color || "var(--accent)"};`;
      if (animate) barStyle += `animation-delay:${delayMs}ms;`;
      const barEl = h("span", {
        class: "bc-vbar" + (animate ? " bc-anim" : ""),
        style: barStyle,
      });

      const trackEl = h("div", { class: "bc-vtrack" },
        ...(mark ? [mark] : []),
        valEl,
        barEl);

      const rawLabel = String(r.label || "");
      const shortLabel = rawLabel.length > 14 ? rawLabel.slice(0, 14) + "…" : rawLabel;
      const xLabelEl = h("span", { class: "bc-xlabel", title: rawLabel }, shortLabel);

      const btn = h("button", {
        type: "button",
        class: "bc-col" + (isSel ? " is-selected" : "") + (v == null ? " is-empty" : ""),
        "aria-expanded": detail ? String(isSel) : null,
        "aria-label": `${r.label}: ${v == null ? "no value" : r.display ?? format(v)}`,
        "data-row": r.id,
        on: {
          click: () => onSelect && onSelect(isSel ? null : r.id),
          pointerenter: (e) => {
            if (e.pointerType === "mouse") {
              wrap.classList.add("bc-hovering");
              btn.classList.add("is-hover");
              showTipV(btn, r);
            }
          },
          pointerleave: () => {
            wrap.classList.remove("bc-hovering");
            btn.classList.remove("is-hover");
            tip.hidden = true;
          },
          focus: () => {
            wrap.classList.add("bc-hovering");
            btn.classList.add("is-hover");
            showTipV(btn, r);
          },
          blur: () => {
            wrap.classList.remove("bc-hovering");
            btn.classList.remove("is-hover");
            tip.hidden = true;
          },
        },
      },
        trackEl,
        xLabelEl);

      plot.appendChild(btn);
    });

    function showTipV(colEl, r) {
      if (!tooltip) return;
      const body = tooltip(r);
      if (!body) { tip.hidden = true; return; }
      tip.textContent = "";
      tip.appendChild(body);
      tip.hidden = false;
      const box = wrap.getBoundingClientRect();
      const cr = colEl.getBoundingClientRect();
      const x = cr.left - box.left + cr.width / 2 - tip.offsetWidth / 2;
      const room = box.width - tip.offsetWidth - 8;
      tip.style.left = `${clamp(x, 8, Math.max(8, room))}px`;
      const above = cr.top - box.top - tip.offsetHeight - 8;
      tip.style.top = `${Math.max(8, above)}px`;
    }

    const scroll = h("div", { class: "bc-v-scroll" }, axis, plot);
    scroll.addEventListener("scroll", () => barScroll.set(id, scroll.scrollLeft), { passive: true });
    const savedX = barScroll.get(id);
    if (savedX) queueMicrotask(() => requestAnimationFrame(() => { if (scroll.isConnected) scroll.scrollLeft = savedX; }));
    wrap.appendChild(scroll);
    wrap.appendChild(tip);

    if (selectedId != null && detail) {
      const selRow = rows.find((r) => r.id === selectedId);
      if (selRow) {
        const detailNode = detail(selRow);
        if (detailNode) {
          wrap.appendChild(h("div", { class: "bc-v-detail" },
            h("h3", {}, selRow.label),
            detailNode));
        }
      }
    }

    return wrap;
  }
  const stagger = rows.length > 1 ? (BAR_MS * 0.4) / (rows.length - 1) : 0;

  const wrap = h("div", { class: "bc", role: "group", "aria-label": ariaLabel });
  if (!rows.length) {
    wrap.appendChild(h("div", { class: "bc-empty" }, emptyText));
    return wrap;
  }

  const axis = h("div", { class: "bc-axis", "aria-hidden": "true" },
    h("span", { class: "bc-axis-pad" }),
    h("span", { class: "bc-axis-track" },
      tickList.map((t) => h("span", { class: "bc-tick", style: `left:${clamp((t / axisMax) * 100, 0, 100)}%` }, format(t)))),
    h("span", { class: "bc-axis-pad bc-axis-end" }));
  const grid = h("div", { class: "bc-grid", "aria-hidden": "true" },
    h("span", { class: "bc-axis-pad" }),
    h("span", { class: "bc-grid-track" },
      tickList.map((t) => h("span", { class: "bc-gridline", style: `left:${clamp((t / axisMax) * 100, 0, 100)}%` }))),
    h("span", { class: "bc-axis-pad bc-axis-end" }));

  const tip = h("div", { class: "bc-tip", role: "tooltip", hidden: true });
  const plot = h("div", { class: "bc-plot" }, grid);

  rows.forEach((r, i) => {
    const v = values[i];
    const frac = v == null ? 0 : clamp(v / axisMax, 0, 1);
    const isSel = selectedId != null && r.id === selectedId;
    const bar = h("span", {
      class: "bc-bar" + (animate ? " bc-grow" : ""),
      style: `--bc-frac:${frac};--bc-color:${r.color || "var(--accent)"};--bc-delay:${Math.round(i * stagger)}ms;--bc-ms:${BAR_MS}ms`,
    });
    const badges = (r.badges || []).map((b) => h("span", { class: `bc-badge${b.tone ? " " + b.tone : ""}` }, b.text));
    const btn = h("button", {
      type: "button",
      class: "bc-row" + (isSel ? " is-selected" : "") + (v == null ? " is-empty" : ""),
      "aria-expanded": detail ? String(isSel) : null,
      "aria-label": `${r.label}: ${v == null ? "no value" : r.display ?? format(v)}`,
      "data-row": r.id,
      on: {
        click: () => onSelect && onSelect(isSel ? null : r.id),
        pointerenter: (e) => { if (e.pointerType === "mouse") showTip(btn, r, frac); },
        pointerleave: () => { tip.hidden = true; },
        focus: () => showTip(btn, r, frac),
        blur: () => { tip.hidden = true; },
      },
    },
      h("span", { class: "bc-label" },
        r.color ? h("span", { class: "bc-swatch", style: `background:${r.color}` }) : null,
        h("span", { class: "bc-name" }, r.label),
        badges.length ? h("span", { class: "bc-badges" }, badges) : null),
      h("span", { class: "bc-track" }, bar),
      h("span", { class: "bc-value num" }, v == null ? "—" : (r.display ?? format(v))));
    plot.appendChild(btn);
    if (isSel && detail) {
      const node = detail(r);
      if (node) plot.appendChild(h("div", { class: "bc-detail" }, node));
    }
  });

  function showTip(rowEl, r, frac) {
    if (!tooltip) return;
    const body = tooltip(r);
    if (!body) { tip.hidden = true; return; }
    tip.textContent = "";
    tip.appendChild(body);
    tip.hidden = false;
    const track = rowEl.querySelector(".bc-track");
    const box = wrap.getBoundingClientRect();
    const tr = track.getBoundingClientRect();
    const rr = rowEl.getBoundingClientRect();
    const x = tr.left - box.left + tr.width * frac;
    const room = box.width - tip.offsetWidth - 8;
    tip.style.left = `${clamp(x + 12, 8, Math.max(8, room))}px`;
    const below = rr.bottom - box.top + 6;
    const above = rr.top - box.top - tip.offsetHeight - 6;
    tip.style.top = `${below + tip.offsetHeight > box.height && above > 0 ? above : below}px`;
  }

  wrap.appendChild(axis);
  wrap.appendChild(plot);
  wrap.appendChild(tip);
  return wrap;
}

/* Up to five round ticks from 0 to max, the last one landing on max itself. */
function niceTicks(max) {
  const raw = max / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => max / s <= 4) || raw;
  const out = [];
  for (let t = 0; t < max - step * 0.25; t += step) out.push(Math.round(t * 1e6) / 1e6);
  out.push(max);
  return out;
}
