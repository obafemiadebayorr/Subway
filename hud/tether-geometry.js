/* Tether geometry: live anchors, stretchy curves, ring layout, collision push. Pure maths, no DOM writes. */
export const MIN_GAP_REM = 4.5;
export const remPx = () => parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
const cx = (r) => r.x + r.w / 2;
const cy = (r) => r.y + r.h / 2;

/* An element's rect relative to the layer the tether SVG is drawn in. */
export function localRect(el, layer) {
  const r = el.getBoundingClientRect();
  const o = layer.getBoundingClientRect();
  return { x: r.left - o.left, y: r.top - o.top, w: r.width, h: r.height };
}

/* Where the line from `from`'s centre toward `to`'s centre crosses `from`'s edge: any point on the outline, not only side midpoints. */
export function anchorOn(from, to) {
  const fx = cx(from), fy = cy(from);
  const dx = cx(to) - fx, dy = cy(to) - fy;
  const hw = from.w / 2 || 1, hh = from.h / 2 || 1;
  if (!dx && !dy) return { x: fx, y: from.y, nx: 0, ny: -1 };
  const inset = Math.min(12, hw / 3, hh / 3); // keep off the rounded corners
  if (Math.abs(dx) / hw >= Math.abs(dy) / hh) {
    const sx = Math.sign(dx);
    const y = Math.max(from.y + inset, Math.min(from.y + from.h - inset, fy + dy * (hw / Math.abs(dx))));
    return { x: fx + sx * hw, y, nx: sx, ny: 0 };
  }
  const sy = Math.sign(dy);
  const x = Math.max(from.x + inset, Math.min(from.x + from.w - inset, fx + dx * (hh / Math.abs(dy))));
  return { x, y: fy + sy * hh, nx: 0, ny: sy };
}

/* Cubic Bézier leaving each anchor along its side's normal. Slack lines sag; pulled past rest they straighten. */
export function tetherPath(a, b, restLength, reduced = false) {
  const dist = Math.hypot(b.x - a.x, b.y - a.y);
  if (reduced) return `M${a.x.toFixed(1)},${a.y.toFixed(1)} L${b.x.toFixed(1)},${b.y.toFixed(1)}`;
  const tension = dist > restLength && dist > 0 ? restLength / dist : 1;
  const reach = Math.max(12, Math.min(dist * 0.45, 160)) * tension;
  const sag = Math.max(0, restLength - dist) * 0.35;
  const c1x = a.x + a.nx * reach, c1y = a.y + a.ny * reach + sag;
  const c2x = b.x + b.nx * reach, c2y = b.y + b.ny * reach + sag;
  const f = (n) => n.toFixed(1);
  return `M${f(a.x)},${f(a.y)} C${f(c1x)},${f(c1y)} ${f(c2x)},${f(c2y)} ${f(b.x)},${f(b.y)}`;
}

export function clampRect(r, b) {
  r.x = Math.min(Math.max(r.x, b.x), b.x + b.w - r.w);
  r.y = Math.min(Math.max(r.y, b.y), b.y + b.h - r.h);
  return r;
}

/* The sketch's positions, in degrees (0 = right, 90 = down). */
const ANGLE = { opencode: -135, devin: -90, deepai: -55, pi: -55, antigravity: -25, claude: 0, cursor: 40, local: 90, copilot: 140, github: 140, kimi: 180 };
const FREE = [-160, -115, -75, -10, 20, 65, 115, 160];

/* Initial spots on an ellipse just outside the composer, clamped into bounds. pods: [{id, w, h}]. */
export function ringLayout(box, pods, bounds, gap) {
  const used = new Set();
  return pods.map((p, i) => {
    let deg = ANGLE[p.id];
    if (deg === undefined || used.has(deg)) deg = FREE.find((f) => !used.has(f)) ?? (i * 47) % 360;
    used.add(deg);
    const t = (deg * Math.PI) / 180;
    const rx = box.w / 2 + gap + p.w / 2;
    const ry = box.h / 2 + gap + p.h / 2;
    return clampRect({ id: p.id, w: p.w, h: p.h, x: cx(box) + rx * Math.cos(t) - p.w / 2, y: cy(box) + ry * Math.sin(t) - p.h / 2 }, bounds);
  });
}

/* The dock size the ring needs around the composer. */
export function ringSize(box, pods, gap) {
  const mw = Math.max(0, ...pods.map((p) => p.w));
  const mh = Math.max(0, ...pods.map((p) => p.h));
  return { w: box.w + 2 * (gap + mw) + gap, h: box.h + 2 * (gap + mh) + gap };
}

/* Push overlapping rects apart (fixed ones, like the composer, never move). */
export function resolveCollisions(rects, fixed, bounds, gap, passes = 30) {
  for (let p = 0; p < passes; p++) {
    let moved = false;
    for (let i = 0; i < rects.length; i++) {
      const a = rects[i];
      const others = fixed.map((f) => [f, true]).concat(rects.filter((_, j) => j > i).map((r) => [r, false]));
      for (const [b, isFixed] of others) {
        const ox = Math.min(a.x + a.w, b.x + b.w) + gap - Math.max(a.x, b.x);
        const oy = Math.min(a.y + a.h, b.y + b.h) + gap - Math.max(a.y, b.y);
        if (ox <= 0 || oy <= 0) continue;
        moved = true;
        const share = isFixed ? 1 : 0.5;
        if (ox < oy) { const s = (cx(a) < cx(b) ? -1 : 1) * ox * share; a.x += s; if (!isFixed) b.x -= s; }
        else { const s = (cy(a) < cy(b) ? -1 : 1) * oy * share; a.y += s; if (!isFixed) b.y -= s; }
        clampRect(a, bounds);
        if (!isFixed) clampRect(b, bounds);
      }
    }
    if (!moved) break;
  }
  return rects;
}
