// extensions/inline-estimate.js — pure Copilot inline-suggestion estimator (no vscode/API calls).
'use strict';
function classifyChange(change, ctx) {
  if (!change || !ctx) return false;
  const text = String(change.text || '');
  if (text.length < 8) return false;                    // single multi-char insert, >=8
  if (Array.isArray(ctx.changes) && ctx.changes.length !== 1) return false;
  if (ctx.paste || ctx.undo || ctx.redo) return false;  // caller flags
  const now = Number(ctx.now || 0), last = Number(ctx.lastBulkMs || 0);
  if (last && now - last < 300) return false;            // within 300ms of paste/undo/redo
  const uri = ctx.uri || '';
  if (uri && !/^file:/.test(uri)) return false;
  const size = Number(ctx.docSize || 0);
  if (size > 1024 * 1024) return false;                  // doc < 1MB
  if (change.rangeLength > 0 && text.length < 16) return false; // replacement, not a completion
  return true;
}
function aggregate(prev, file, day) {
  const out = { ...(prev && typeof prev === 'object' ? prev : {}), kind: 'copilot_inline_estimate', estimate: true };
  out.days = { ...(out.days || {}) }; out.days[day] = { ...((out.days[day]) || {}) };
  out.days[day][file] = (out.days[day][file] || 0) + 1; out.updated_at = new Date().toISOString();
  return out;
}
module.exports = { classifyChange, aggregate };
