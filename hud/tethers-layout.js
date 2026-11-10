// Serves docs/design/23-tethers.md §6 and the user's box sketch.
// Layout calculations for tether anchors, elbow paths, and provider slots.
// Compatibility layer: re-exports kept for tests; runtime geometry lives in tether-geometry.js.
import { anchorOn as _anchorOn, tetherPath } from "./tether-geometry.js";

const SLOTS = [
  { keys: ["opencode"], xPct: 12, yPct: 20 },          // top-left (user sketch)
  { keys: ["devin"], xPct: 50, yPct: 12 },             // top-centre
  { keys: ["deep"], xPct: 78, yPct: 16 },              // top-right: Deepseek/DeepAI
  { keys: ["antigravity", "agy"], xPct: 88, yPct: 34 },// top-right
  { keys: ["claude"], xPct: 88, yPct: 60 },            // right
  { keys: ["cursor"], xPct: 78, yPct: 84 },            // bottom-right
  { keys: ["copilot"], xPct: 20, yPct: 84 },           // bottom-left
];

export function clamp(n, lo, hi) { return Math.min(hi, Math.max(lo, n)); }

export function slotFor(id, name, index, count) {
  const hay = String(id || "").toLowerCase() + " " + String(name || "").toLowerCase();
  for (const s of SLOTS) for (const k of s.keys) if (hay.includes(k)) return { xPct: s.xPct, yPct: s.yPct };
  // Unknown provider: fall back to an even arc left of the composer so it never overlaps (no fake providers, just placement).
  const n = Math.max(1, count | 0);
  const t = (index | 0) / n;
  return { xPct: 14 + 0 * t, yPct: 30 + t * 40 };
}

export function anchorOn(rect, point) {
  // Compat: old signature (rect, point) -> {x,y,side}. New geometry uses (from,to) rects.
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  const hw = Math.max(rect.width / 2, 1);
  const hh = Math.max(rect.height / 2, 1);
  const relX = point.x - cx;
  const relY = point.y - cy;
  if (Math.abs(relX) / hw >= Math.abs(relY) / hh) {
    const side = relX >= 0 ? "right" : "left";
    return { x: side === "right" ? rect.right : rect.left, y: clamp(point.y, rect.top, rect.bottom), side };
  }
  const side = relY >= 0 ? "bottom" : "top";
  return { x: clamp(point.x, rect.left, rect.right), y: side === "bottom" ? rect.bottom : rect.top, side };
}

export function nearestSide(rect, point) { return anchorOn(rect, point).side; }

export function elbowPath(from, to, radius) {
  const r0 = Number.isFinite(radius) ? radius : 18;
  const dx = to.x - from.x, dy = to.y - from.y;
  const adx = Math.abs(dx), ady = Math.abs(dy);
  if (adx < 1 || ady < 1) return "M " + from.x + " " + from.y + " L " + to.x + " " + to.y;
  const sx = Math.sign(dx), sy = Math.sign(dy);
  const mx = from.x + sx * (adx / 2);
  const r = Math.min(r0, adx / 2, ady / 2);
  return [
    "M " + from.x + " " + from.y,
    "L " + (mx - sx * r) + " " + from.y,
    "Q " + mx + " " + from.y + " " + mx + " " + (from.y + sy * r),
    "L " + mx + " " + (to.y - sy * r),
    "Q " + mx + " " + to.y + " " + (mx + sx * r) + " " + to.y,
    "L " + to.x + " " + to.y,
  ].join(" ");
}
