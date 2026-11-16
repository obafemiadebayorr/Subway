/* hud/landing.js - Landing: worker branches waiting to land, and overlaps a resolver can fix (roadmap 62).
   Current HUD conventions only (card / rows / tags / btns); the full redesign happens in Paper later. */
import { h, api, act } from "./lib.js";

const STATUS = { needs_resolution: "Needs resolution", resolving: "Resolving", resolved: "Resolved", dropped: "Dropped" };
const RELOAD_MS = 5000;
let pageEl = null;
let bodyEl = null;
let loadedAt = 0;

function section(title, sub, rows, empty) {
  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h2", { class: "card-title" }, title), sub ? h("div", { class: "card-sub" }, sub) : null)),
    h("div", { class: "rows" }, rows.length ? rows : h("div", { class: "empty" }, empty)));
}

function overlapRow(c) {
  const files = (c.conflicts || []).join(", ") || "no files listed";
  const dels = (c.deletes_landed || []).length ? ` \u00b7 deletions landed: ${c.deletes_landed.join(", ")}` : "";
  const tone = c.status === "needs_resolution" ? "bad" : c.status === "resolved" ? "ok" : "";
  return h("div", { class: "row" },
    h("div", {}, h("div", { class: "row-title mono" }, c.branch || c.id), h("div", { class: "row-sub" }, `${c.role || "worker"} \u00b7 ${files}${dels}`)),
    h("div", { class: "tags" },
      h("span", { class: `tag ${tone}` }, STATUS[c.status] || c.status),
      c.resolver ? h("span", { class: "tag mono" }, c.resolver) : null,
      h("span", { class: "tag mono" }, c.id)),
    c.status === "needs_resolution" ? h("div", { class: "btns" },
      h("button", { class: "btn", type: "button", on: { click: () => act("/api/landing/resolve", { id: c.id }).then(() => load(true)) } }, "Resolve"),
      h("button", { class: "btn danger", type: "button", on: { click: () => act("/api/landing/drop", { id: c.id }).then(() => load(true)) } }, "Drop")) : null);
}

function queueRow(e) {
  return h("div", { class: "row" },
    h("div", {}, h("div", { class: "row-title mono" }, e.branch), h("div", { class: "row-sub" }, `${e.role || "worker"} \u00b7 ${e.worker_id || ""}`)),
    h("div", { class: "tags" }, h("span", { class: "tag" }, "Queued")));
}

function render(data) {
  const conflicts = (data && data.conflicts) || [];
  const open = conflicts.filter((c) => c.status === "needs_resolution" || c.status === "resolving");
  const closed = conflicts.filter((c) => !open.includes(c)).slice(-10).reverse();
  bodyEl.replaceChildren(
    section("Overlaps", "Worker changes that overlapped HEAD. Resolve queues a worker that merges both sides in its own worktree; its branch then lands like any other.", open.map(overlapRow), "No overlaps waiting."),
    section("Landing queue", `Land mode: ${(data && data.land_mode) || "merge"}. Queued branches land when the tree is quiet.`, ((data && data.queue) || []).map(queueRow), "Nothing queued."),
    closed.length ? section("Recently closed", "", closed.map(overlapRow), "") : null);
}

function load(force) {
  if (!force && Date.now() - loadedAt < RELOAD_MS) return Promise.resolve();
  loadedAt = Date.now();
  return api("/api/landing").then(render)
    .catch((e) => bodyEl.replaceChildren(h("div", { class: "empty" }, `Couldn't load landing: ${e.message || e}`)));
}

export function landingPage() {
  if (!pageEl) {
    bodyEl = h("div", { class: "landing-body" }, h("div", { class: "empty" }, "Loading\u2026"));
    pageEl = h("div", { class: "landing-page" }, bodyEl);
  }
  load(false);
  return pageEl;
}
