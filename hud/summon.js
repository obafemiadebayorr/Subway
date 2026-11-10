/* T1 — floating summon button + draggable composer panel (tethers item 48). */
import { h, icon } from "./lib.js";
import { aiPrompt } from "./ai-prompt.js";
import { mountTethers } from "./tethers.js";

let summonTetherHandle = null;

const POS_KEY = "tt-summon-pos";
const PANEL_POS_KEY = "hud.summon.pos";

function remSize() {
  if (typeof getComputedStyle === "undefined") return 16;
  const v = parseFloat(getComputedStyle(document.documentElement).fontSize);
  return v > 0 ? v : 16;
}

function loadPos() {
  try {
    const raw = localStorage.getItem(POS_KEY);
    if (!raw) return null;
    const pos = JSON.parse(raw);
    if (typeof pos.x === "number" && typeof pos.y === "number") return pos;
  } catch (e) { /* private windows / corrupt */ }
  return null;
}

function savePos(pos) {
  try { localStorage.setItem(POS_KEY, JSON.stringify(pos)); } catch (e) { /* private windows */ }
}

function loadPanelPos() {
  try {
    const raw = localStorage.getItem(PANEL_POS_KEY);
    if (!raw) return null;
    const pos = JSON.parse(raw);
    if (typeof pos.x === "number" && typeof pos.y === "number") return pos;
  } catch (e) { /* private windows / corrupt */ }
  return null;
}

function savePanelPos(pos) {
  try { localStorage.setItem(PANEL_POS_KEY, JSON.stringify(pos)); } catch (e) { /* private windows */ }
}

function clearPanelPos() {
  try { localStorage.removeItem(PANEL_POS_KEY); } catch (e) { /* private windows */ }
}

function clamp(v, min, max) {
  if (min > max) return min;
  return Math.min(Math.max(v, min), max);
}

function getNavHeight() {
  if (typeof document === "undefined") return 0;
  const nav = document.querySelector(".rail") || document.querySelector("nav");
  if (!nav) return 0;
  const r = nav.getBoundingClientRect();
  const winH = window.innerHeight || 0;
  const winW = window.innerWidth || 0;
  if (r.bottom >= winH - 8 && r.top > 0 && r.width >= winW * 0.5) {
    return r.height;
  }
  return 0;
}

function updateNavHeight() {
  if (typeof document === "undefined") return 0;
  const hgt = getNavHeight();
  document.documentElement.style.setProperty("--tt-nav-h", `${hgt}px`);
  return hgt;
}

/* Snap to the nearest viewport edge, keeping the button fully on screen and above nav. */
function snap(pos, el) {
  const w = el.offsetWidth || 48;
  const hgt = el.offsetHeight || 48;
  const navH = getNavHeight();
  const maxX = Math.max(0, window.innerWidth - w);
  const maxY = Math.max(0, window.innerHeight - hgt - navH);
  const x = clamp(pos.x, 0, maxX);
  const y = clamp(pos.y, 0, maxY);
  const left = x < maxX - x;
  const top = y < maxY - y;
  return { x: left ? 0 : maxX, y: top ? 0 : maxY };
}

function applyPos(el, pos) {
  el.style.left = `${pos.x}px`;
  el.style.top = `${pos.y}px`;
  el.style.right = "auto";
  el.style.bottom = "auto";
}

export function mountSummon(host) {
  if (!host || host.querySelector(".tt-summon")) return null; // mount once

  updateNavHeight();

  let open = false;
  let pos = loadPos();
  let composerEl = null;

  const scrim = h("div", {
    class: "tt-scrim",
    hidden: true,
    "aria-hidden": "true",
  });

  const sheet = h("div", {
    class: "tt-sheet",
    role: "dialog",
    "aria-modal": "false",
    "aria-label": "Composer",
    hidden: true,
  },
    h("div", { class: "tt-sheet-head", tabindex: "0", "aria-label": "Move composer" },
      h("span", { class: "tt-sheet-title" }, "Composer"),
      h("button", {
        class: "tt-sheet-close", type: "button", "aria-label": "Close composer",
        on: { click: () => setOpen(false, true) },
      }, icon("x")),
    ),
    h("div", { class: "tt-sheet-body" }),
  );

  const head = sheet.querySelector(".tt-sheet-head");
  const SIZE_KEY = "hud.summon.size";
  const grip = h("div", { class: "tt-sheet-grip", role: "separator", "aria-label": "Resize composer", title: "Drag to resize" });
  sheet.appendChild(grip);
  const applySize = (sz) => {
    if (!sz || typeof sz.w !== "number" || typeof sz.h !== "number") return;
    const w = Math.max(288, Math.min(sz.w, window.innerWidth * 0.95));
    const hh = Math.max(256, Math.min(sz.h, window.innerHeight * 0.92));
    sheet.style.width = `${w}px`;
    sheet.style.height = `${hh}px`;
    sheet.style.maxHeight = "none";
    sheet.classList.add("tt-sized");
  };
  try { applySize(JSON.parse(localStorage.getItem(SIZE_KEY) || "null")); } catch (e) { /* private windows */ }
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const r = sheet.getBoundingClientRect();
    const sx = e.clientX, sy = e.clientY;
    grip.setPointerCapture(e.pointerId);
    applyPanelPos(r.left, r.top);
    let last = null;
    const move = (ev) => {
      const w = Math.max(288, Math.min(r.width - (ev.clientX - sx), window.innerWidth * 0.95));
      const hh = Math.max(256, Math.min(r.height - (ev.clientY - sy), window.innerHeight * 0.92));
      const x = r.right - w, y = r.bottom - hh;
      applyPanelPos(x, y);
      applySize({ w, h: hh });
      last = { w, h: hh, x, y };
    };
    const up = () => {
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", up);
      grip.removeEventListener("pointercancel", up);
      if (!last) return;
      try { localStorage.setItem(SIZE_KEY, JSON.stringify({ w: last.w, h: last.h })); } catch (err) { /* private windows */ }
      savePanelPos({ x: last.x, y: last.y });
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
    grip.addEventListener("pointercancel", up);
  });

  const btn = h("button", {
    class: "tt-summon", type: "button", "aria-label": "Open composer",
    "aria-expanded": "false",
    on: {
      click: () => { if (!dragged) setOpen(!open, true); },
    },
  }, icon("plus"));

  function ensureComposer() {
    if (!composerEl) {
      composerEl = aiPrompt({ context: "threads" });
      const body = sheet.querySelector(".tt-sheet-body");
      if (body) {
        const tetherHost = h("div", { class: "tt-tether-host" });
        const field = h("div", { class: "tt-composer-field" }, tetherHost, composerEl);
        body.append(field);
        if (!summonTetherHandle) {
          summonTetherHandle = mountTethers(tetherHost, { status: null, anchor: field });
        }
      }
    }
    return composerEl;
  }

  let dragged = false;
  let pointerId = null;
  let start = null;
  let startPos = null;

  btn.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    dragged = false;
    pointerId = ev.pointerId;
    start = { x: ev.clientX, y: ev.clientY };
    const rect = btn.getBoundingClientRect();
    startPos = { x: rect.left, y: rect.top };
    btn.setPointerCapture(pointerId);
  });

  btn.addEventListener("pointermove", (ev) => {
    if (pointerId === null || ev.pointerId !== pointerId) return;
    const dx = ev.clientX - start.x;
    const dy = ev.clientY - start.y;
    if (!dragged && Math.hypot(dx, dy) < 4) return; // still a click
    dragged = true;
    const navH = getNavHeight();
    const maxX = Math.max(0, window.innerWidth - btn.offsetWidth);
    const maxY = Math.max(0, window.innerHeight - btn.offsetHeight - navH);
    pos = { x: clamp(startPos.x + dx, 0, maxX), y: clamp(startPos.y + dy, 0, maxY) };
    applyPos(btn, pos);
  });

  const endDrag = (ev) => {
    if (pointerId === null || (ev && ev.pointerId !== pointerId)) return;
    pointerId = null;
    if (dragged) {
      pos = snap(pos, btn);
      applyPos(btn, pos);
      savePos(pos);
    }
    dragged = false;
  };
  btn.addEventListener("pointerup", endDrag);
  btn.addEventListener("pointercancel", endDrag);

  /* --- composer panel dragging (Pointer Events, rAF-throttled) --- */
  let panelPointerId = null;
  let panelStart = null;
  let panelOrigin = null;
  let panelRafPending = false;
  let panelPendingXY = null;

  function panelClamp(x, y) {
    const r = remSize();
    const visible = 3 * r;
    const w = sheet.offsetWidth || 0;
    const hgt = sheet.offsetHeight || 0;
    const minX = visible - w;
    const maxX = window.innerWidth - visible;
    const minY = visible - hgt;
    const maxY = window.innerHeight - visible;
    return { x: clamp(x, minX, maxX), y: clamp(y, minY, maxY) };
  }

  function applyPanelPos(x, y) {
    sheet.style.left = `${x}px`;
    sheet.style.top = `${y}px`;
    sheet.style.right = "auto";
    sheet.style.bottom = "auto";
    sheet.style.transform = "none";
  }

  function flushPanelPos() {
    if (!panelPendingXY) return;
    savePanelPos({ x: panelPendingXY.x / remSize(), y: panelPendingXY.y / remSize() });
  }

  head.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    if (ev.target.closest("button, input, textarea")) return;
    panelPointerId = ev.pointerId;
    panelStart = { x: ev.clientX, y: ev.clientY };
    const rect = sheet.getBoundingClientRect();
    panelOrigin = { x: rect.left, y: rect.top };
    head.setPointerCapture(panelPointerId);
  });

  head.addEventListener("pointermove", (ev) => {
    if (panelPointerId === null || ev.pointerId !== panelPointerId) return;
    const dx = ev.clientX - panelStart.x;
    const dy = ev.clientY - panelStart.y;
    const { x, y } = panelClamp(panelOrigin.x + dx, panelOrigin.y + dy);
    panelPendingXY = { x, y };
    if (!panelRafPending) {
      panelRafPending = true;
      requestAnimationFrame(() => {
        panelRafPending = false;
        if (panelPendingXY) {
          applyPanelPos(panelPendingXY.x, panelPendingXY.y);
          window.dispatchEvent(new Event("resize"));
        }
      });
    }
  });

  const endPanelDrag = (ev) => {
    if (panelPointerId === null || (ev && ev.pointerId !== panelPointerId)) return;
    panelPointerId = null;
    flushPanelPos();
  };
  head.addEventListener("pointerup", endPanelDrag);
  head.addEventListener("pointercancel", endPanelDrag);

  head.addEventListener("dblclick", () => {
    sheet.style.left = "";
    sheet.style.top = "";
    sheet.style.right = "";
    sheet.style.bottom = "";
    sheet.style.transform = "";
    clearPanelPos();
    window.dispatchEvent(new Event("resize"));
  });

  head.addEventListener("keydown", (ev) => {
    const deltas = {
      ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
    };
    const d = deltas[ev.key];
    if (!d) return;
    ev.preventDefault();
    const step = (ev.shiftKey ? 4 : 1) * remSize();
    const rect = sheet.getBoundingClientRect();
    const { x, y } = panelClamp(rect.left + d[0] * step, rect.top + d[1] * step);
    applyPanelPos(x, y);
    savePanelPos({ x: x / remSize(), y: y / remSize() });
    window.dispatchEvent(new Event("resize"));
  });

  function restorePanelPos() {
    const stored = loadPanelPos();
    if (!stored) return;
    const r = remSize();
    requestAnimationFrame(() => {
      const { x, y } = panelClamp(stored.x * r, stored.y * r);
      applyPanelPos(x, y);
    });
  }

  function setOpen(next, focusBtn = false) {
    if (open === next) return;
    open = next;
    if (next) {
      ensureComposer();
      scrim.hidden = false;
      sheet.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      restorePanelPos();
      const ta = sheet.querySelector("textarea");
      if (ta) {
        ta.focus();
      } else if (typeof requestAnimationFrame !== "undefined") {
        requestAnimationFrame(() => sheet.querySelector("textarea")?.focus());
      }
    } else {
      scrim.hidden = true;
      sheet.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      if (focusBtn) btn.focus();
    }
  }

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && open) {
      ev.preventDefault();
      setOpen(false, true);
    }
  });

  function syncRoute() {
    const page = location.hash.replace(/^#/, "").split("?")[0] || "overview";
    const onThreads = page === "threads";
    if (onThreads) setOpen(false);
    btn.hidden = onThreads;
  }
  window.addEventListener("hashchange", syncRoute);

  function clampCurrentPos() {
    if (!pos) return;
    const navH = getNavHeight();
    const maxX = Math.max(0, window.innerWidth - (btn.offsetWidth || 48));
    const maxY = Math.max(0, window.innerHeight - (btn.offsetHeight || 48) - navH);
    pos = { x: clamp(pos.x, 0, maxX), y: clamp(pos.y, 0, maxY) };
    applyPos(btn, pos);
  }

  window.addEventListener("resize", () => {
    updateNavHeight();
    clampCurrentPos();
    if (!sheet.hidden) {
      const rect = sheet.getBoundingClientRect();
      const { x, y } = panelClamp(rect.left, rect.top);
      if (sheet.style.left) applyPanelPos(x, y);
    }
  });

  if (pos) {
    clampCurrentPos();
  }
  host.append(scrim, sheet, btn);
  syncRoute();

  document.addEventListener("keydown", (e) => {
    const t = e.target;
    const typing = t && (t.closest && t.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']"));
    const cmdK = (e.metaKey || e.ctrlKey) && !e.altKey && (e.key === "k" || e.key === "K");
    const slash = e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !typing;
    if (!cmdK && !slash) return;
    const page = (typeof location !== "undefined" && location.hash.replace(/^#/, "").split("?")[0]) || "overview";
    if (page === "threads" || btn.hidden) return;
    e.preventDefault();
    setOpen(true);
    requestAnimationFrame(() => { const i = document.querySelector(".tt-composer-field .cmp-input"); if (i) i.focus(); });
  });

  return { el: btn, sheet, scrim, setOpen };
}
