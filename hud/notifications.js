/* KokonutUI notification-stack port: plain JS + nstack- CSS. */
import { icon } from "./lib.js";

const PEEK = 10;
const VISIBLE_BEHIND = 2;
const GAP = 8;
const FALLBACK_HEIGHT = 76;
const DISMISS_DISTANCE = 100;
const DISMISS_VELOCITY = 0.11;
const AXIS_LOCK = 6;
const SPRING_STIFFNESS = 520;
const SPRING_DAMPING = 2 * Math.sqrt(SPRING_STIFFNESS);
const FLIGHT_MIN_SPEED = 1400;
const FLIGHT_ACCEL = 4200;
const VELOCITY_WINDOW_MS = 80;
const EXIT_MS = 200;
const EASE_OUT = "cubic-bezier(0.23, 1, 0.32, 1)";
const MAX_LIVE = 20;
const KINDS = new Set(["info", "warn", "bad", "good"]);
const TITLES = { info: "Notice", good: "Done", warn: "Warning", bad: "Problem" };
const ICONS = { info: "bell", good: "check", warn: "clock", bad: "x" };

let root = null;
let listEl = null;
let headerEl = null;
let countEl = null;
let announcerEl = null;
let counter = 0;
const items = []; // newest first
const heights = new Map();
const swipeRefs = new Map();
const lastY = new Map();
const swipeX = new Map();
const frames = new Map();
const dismissed = new Set();
let hovered = false;
let focused = false;
let pinned = false;
let timeTicker = 0;

function reduceMotion() {
  try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; }
}

function opacityFor(x) { return Math.max(1 - Math.abs(x) / 240, 0.3); }

function releaseVelocity(samples) {
  const last = samples[samples.length - 1];
  if (!last) return 0;
  let first = last;
  for (let i = samples.length - 1; i >= 0; i--) {
    const sample = samples[i];
    if (last.t - sample.t > VELOCITY_WINDOW_MS) break;
    first = sample;
  }
  const dt = last.t - first.t;
  return dt > 0 ? (last.x - first.x) / dt : 0;
}

function dismissDirection(dx, velocity) {
  const flick = Math.abs(velocity) > DISMISS_VELOCITY && Math.sign(velocity) === Math.sign(dx);
  if (Math.abs(dx) < DISMISS_DISTANCE && !flick) return 0;
  return (dx === 0 ? velocity : dx) > 0 ? 1 : -1;
}

function relTime(ms) {
  const s = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (s < 45) return "now";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m";
  const h = Math.floor(m / 60);
  if (h < 24) return h + "h";
  return Math.floor(h / 24) + "d";
}

function isPaused() { return hovered || focused || pinned; }

function syncTicker() {
  if (items.some((it) => !it.gone) && !timeTicker) {
    timeTicker = setInterval(() => {
      for (const it of items) {
        if (!it.gone && it.timeEl) it.timeEl.textContent = relTime(it.createdAt);
      }
      if (!items.some((x) => !x.gone)) { clearInterval(timeTicker); timeTicker = 0; }
    }, 30000);
  } else if (!items.some((it) => !it.gone) && timeTicker) {
    clearInterval(timeTicker);
    timeTicker = 0;
  }
}

function syncTimers(paused) {
  const now = Date.now();
  for (const item of items) {
    if (item.gone) continue;
    if (paused) {
      if (!item.pausedAt) {
        item.pausedAt = now;
        clearTimeout(item.timer);
        item.timer = 0;
      }
    } else if (item.pausedAt) {
      item.remaining = Math.max(0, item.remaining - (item.pausedAt - item.startedAt));
      item.pausedAt = 0;
      item.startedAt = now;
      arm(item);
    } else if (!item.timer && item.remaining > 0 && item.timeout > 0) {
      // Added while paused: start only once pause ends.
      item.startedAt = now;
      arm(item);
    }
  }
}

function arm(item) {
  clearTimeout(item.timer);
  item.timer = 0;
  if (item.timeout <= 0 || item.gone) return;
  item.timer = setTimeout(() => dismiss(item.id, 1, 0, true), item.remaining);
}

function laidOut() { return items.filter((item) => !item.leaving); }

function expanded() { return isPaused() && laidOut().length > 1; }

function heightOf(id) { return heights.get(id) ?? FALLBACK_HEIGHT; }

function stopFrame(id) {
  const frame = frames.get(id);
  if (frame !== undefined) { cancelAnimationFrame(frame); frames.delete(id); }
}

function paint(id, x) {
  swipeX.set(id, x);
  const node = swipeRefs.get(id);
  if (node) {
    node.style.transition = "none";
    node.style.transform = x === 0 ? "" : `translateX(${x}px)`;
    node.style.opacity = x === 0 ? "" : `${opacityFor(x)}`;
  }
}

function run(id, step, onDone) {
  stopFrame(id);
  let last = performance.now();
  const tick = (now) => {
    const dt = Math.min((now - last) / 1000, 1 / 30);
    last = now;
    if (step(dt)) { frames.delete(id); if (onDone) onDone(); return; }
    frames.set(id, requestAnimationFrame(tick));
  };
  frames.set(id, requestAnimationFrame(tick));
}

function snapBack(id, velocity) {
  let x = swipeX.get(id) ?? 0;
  let v = velocity * 1000;
  if (reduceMotion()) { paint(id, 0); return; }
  run(id, (dt) => {
    const force = -SPRING_STIFFNESS * x - SPRING_DAMPING * v;
    v += force * dt;
    x += v * dt;
    if (Math.abs(x) < 0.5 && Math.abs(v) < 10) { paint(id, 0); return true; }
    paint(id, x);
    return false;
  });
}

function finishDismiss(id) {
  const item = items.find((x) => x.id === id);
  if (item) {
    item.gone = true;
    clearTimeout(item.timer);
    item.timer = 0;
    if (item.li) item.li.remove();
  }
  const idx = items.findIndex((x) => x.id === id);
  if (idx >= 0) items.splice(idx, 1);
  heights.delete(id);
  lastY.delete(id);
  swipeX.delete(id);
  swipeRefs.delete(id);
  dismissed.delete(id);
  syncTicker();
  layout();
}

function dismiss(id, direction, velocity = 0, fromTimer = false) {
  if (dismissed.has(id)) return;
  const item = items.find((x) => x.id === id);
  if (!item || item.gone) return;
  const laid = laidOut();
  dismissed.add(id);
  item.leaving = true;
  clearTimeout(item.timer);
  item.timer = 0;
  if (item.stopListening) { item.stopListening(); item.stopListening = null; }

  const node = swipeRefs.get(id);
  if (node && node.contains(document.activeElement)) {
    const at = laid.findIndex((x) => x.id === id);
    const next = laid[at + 1] ?? laid[at - 1];
    const target = next ? swipeRefs.get(next.id)?.querySelector("article") : listEl;
    if (target) target.focus();
  }

  layout();
  const dir = direction === 0 ? 1 : direction;

  if (!node || reduceMotion()) {
    stopFrame(id);
    if (node) {
      node.style.transition = `opacity ${EXIT_MS}ms ${EASE_OUT}`;
      node.style.opacity = "0";
    }
    setTimeout(() => finishDismiss(id), EXIT_MS);
    return;
  }

  const distance = (node.offsetWidth || 360) * 1.1;
  let x = swipeX.get(id) ?? 0;
  let speed = Math.max(Math.abs(velocity) * 1000, FLIGHT_MIN_SPEED);
  run(id, (dt) => {
    speed += FLIGHT_ACCEL * dt;
    x += dir * speed * dt;
    const done = Math.abs(x) >= distance;
    paint(id, done ? dir * distance : x);
    node.style.opacity = `${Math.max(1 - Math.abs(x) / distance, 0)}`;
    return done;
  }, () => finishDismiss(id));
}

function clearAll() {
  const live = laidOut().slice();
  for (const it of live) dismiss(it.id, 1, 0);
}

function collapse(e) {
  if (!root) return;
  if (e && e.type === "keydown" && e.key !== "Escape") return;
  if (e && e.type === "keydown") {
    pinned = false;
    hovered = root.matches(":hover");
    focused = root.contains(document.activeElement);
    syncTimers(isPaused());
    layout();
    return;
  }
  if (e && e.type === "pointerdown" && root.contains(e.target)) return;
  if (e && e.type === "pointerdown") { pinned = false; focused = false; if (!root.matches(":hover")) hovered = false; }
  syncTimers(isPaused());
  layout();
}

function updateChrome() {
  if (!headerEl || !countEl) return;
  const exp = expanded();
  const n = laidOut().length;
  countEl.textContent = String(n);
  headerEl.hidden = !exp;
  headerEl.setAttribute("aria-hidden", exp ? "false" : "true");
}

function layout() {
  if (!listEl) return;
  const exp = expanded();
  const live = laidOut();
  const frontHeight = live[0] ? heightOf(live[0].id) : 0;
  const stackHeight = exp
    ? live.reduce((sum, item) => sum + heightOf(item.id), 0) + Math.max(live.length - 1, 0) * GAP
    : frontHeight + Math.min(live.length - 1, VISIBLE_BEHIND) * PEEK;
  const offsets = [];
  let running = 0;
  for (const item of live) { offsets.push(running); running += heightOf(item.id) + GAP; }

  listEl.style.height = (live.length ? Math.max(stackHeight, 0) : 0) + "px";
  root.classList.toggle("is-expanded", exp);
  root.classList.toggle("is-empty", live.length === 0);
  updateChrome();

  for (const item of items) {
    if (item.gone || !item.li) continue;
    const index = live.findIndex((c) => c.id === item.id);
    const isLeaving = index === -1;
    const depth = Math.max(index, 0);
    const hiddenBehind = !(exp || depth <= VISIBLE_BEHIND);
    const scale = exp || isLeaving ? 1 : 1 - depth * 0.05;
    const restingY = exp ? (offsets[depth] ?? 0) : frontHeight + depth * PEEK - heightOf(item.id) * scale;
    const y = isLeaving ? (lastY.get(item.id) ?? 0) : restingY;
    if (!isLeaving) lastY.set(item.id, restingY);

    item.li.style.transform = `translateY(${y}px) scale(${scale})`;
    item.li.style.opacity = hiddenBehind ? "0" : "1";
    item.li.style.zIndex = String(items.length - depth);
    item.li.style.pointerEvents = hiddenBehind ? "none" : "";
    if (item.card) item.card.tabIndex = hiddenBehind ? -1 : 0;
    if (item.closeBtn) item.closeBtn.tabIndex = hiddenBehind ? -1 : 0;
    if (item.content) item.content.classList.toggle("is-muted", !exp && depth > 0);
    if (item.closeBtn) item.closeBtn.classList.toggle("is-muted", !exp && depth > 0);
  }
}

function measureAll() {
  if (!listEl) return;
  for (const li of listEl.children) {
    const id = li.getAttribute("data-id");
    if (id) heights.set(id, li.offsetHeight || FALLBACK_HEIGHT);
  }
  layout();
}

export function mountNotifications(parent = document.body) {
  if (root) return root;
  root = document.createElement("div");
  root.className = "nstack";
  root.setAttribute("role", "region");
  root.setAttribute("aria-label", "Notifications");

  announcerEl = document.createElement("div");
  announcerEl.className = "nstack-live";
  announcerEl.setAttribute("aria-live", "polite");
  announcerEl.setAttribute("aria-relevant", "additions");

  headerEl = document.createElement("div");
  headerEl.className = "nstack-header";
  headerEl.hidden = true;
  headerEl.setAttribute("aria-hidden", "true");
  const label = document.createElement("p");
  label.className = "nstack-header-label";
  label.append(document.createTextNode("Notifications "));
  countEl = document.createElement("span");
  countEl.className = "nstack-header-count";
  countEl.textContent = "0";
  label.append(countEl);
  const clearBtn = document.createElement("button");
  clearBtn.type = "button";
  clearBtn.className = "nstack-clear";
  clearBtn.textContent = "Clear all";
  clearBtn.addEventListener("click", (e) => { e.stopPropagation(); clearAll(); });
  headerEl.append(label, clearBtn);

  listEl = document.createElement("ol");
  listEl.className = "nstack-list";
  listEl.setAttribute("aria-label", "Notifications");
  listEl.tabIndex = -1;

  root.append(announcerEl, headerEl, listEl);

  root.addEventListener("focusin", () => { focused = true; syncTimers(true); layout(); });
  root.addEventListener("focusout", (e) => {
    if (root.contains(e.relatedTarget)) return;
    focused = false;
    syncTimers(isPaused());
    layout();
  });
  root.addEventListener("pointerenter", (e) => {
    if (e.pointerType === "mouse") { hovered = true; syncTimers(true); layout(); }
  });
  root.addEventListener("pointerleave", (e) => {
    if (e.pointerType === "mouse") { hovered = false; syncTimers(isPaused()); layout(); }
  });

  document.addEventListener("pointerdown", collapse);
  document.addEventListener("keydown", collapse);

  parent.append(root);
  return root;
}

function makeCard(item) {
  const li = document.createElement("li");
  li.className = "nstack-item";
  li.setAttribute("data-id", item.id);

  const swipe = document.createElement("div");
  swipe.className = "nstack-swipe";

  const card = document.createElement("article");
  card.className = "nstack-card";
  card.setAttribute("aria-label", `${item.title}. ${item.body}`);
  card.tabIndex = 0;

  const content = document.createElement("div");
  content.className = "nstack-content";

  const tile = document.createElement("div");
  tile.className = "nstack-tile nstack-tile-" + item.kind;
  const glyph = icon(ICONS[item.kind] || "bell");
  glyph.classList.add("nstack-glyph");
  glyph.setAttribute("aria-hidden", "true");
  tile.append(glyph);

  const body = document.createElement("div");
  body.className = "nstack-body";
  const top = document.createElement("div");
  top.className = "nstack-top";
  const titleEl = document.createElement("p");
  titleEl.className = "nstack-title";
  titleEl.textContent = item.title;
  const timeEl = document.createElement("span");
  timeEl.className = "nstack-time";
  timeEl.textContent = relTime(item.createdAt);
  top.append(titleEl, timeEl);
  const desc = document.createElement("p");
  desc.className = "nstack-desc";
  desc.textContent = item.body;
  body.append(top, desc);

  content.append(tile, body);

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "nstack-close";
  closeBtn.setAttribute("aria-label", `Dismiss ${item.title}`);
  const xIcon = icon("x");
  xIcon.classList.add("nstack-x");
  xIcon.setAttribute("aria-hidden", "true");
  closeBtn.append(xIcon);
  closeBtn.addEventListener("click", (e) => { e.stopPropagation(); dismiss(item.id, 1, 0); });

  card.append(content, closeBtn);
  swipe.append(card);
  li.append(swipe);

  item.li = li;
  item.card = card;
  item.closeBtn = closeBtn;
  item.content = content;
  item.timeEl = timeEl;

  swipeRefs.set(item.id, swipe);

  // Swipe/pointer handling (faithful to spec)
  let drag = null;
  const onDown = (event) => {
    if (drag || item.leaving || item.gone) return;
    // Only front card swipeable when collapsed
    const live = laidOut();
    const index = live.findIndex((c) => c.id === item.id);
    if (!expanded() && index > 0) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if (event.target.closest("button")) return;
    stopFrame(item.id);
    const { pointerId } = event;
    const release = (up) => {
      if (up.pointerId !== pointerId) return;
      if (item.stopListening) { item.stopListening(); item.stopListening = null; }
      if (drag && drag.pointerId === pointerId) {
        drag = null;
        if (swipeX.get(item.id)) snapBack(item.id, 0);
      }
    };
    if (item.stopListening) item.stopListening();
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    item.stopListening = () => {
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
      item.stopListening = null;
    };
    drag = { pointerId, startX: event.clientX, startY: event.clientY, originX: swipeX.get(item.id) ?? 0, dx: 0, axis: null, samples: [], suppress: false };
  };
  const onMove = (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (drag.axis === null) {
      if (Math.abs(dx) < AXIS_LOCK && Math.abs(dy) < AXIS_LOCK) return;
      drag.axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
      if (drag.axis === "x") swipe.setPointerCapture(event.pointerId);
    }
    if (drag.axis !== "x") return;
    drag.dx = drag.originX + dx;
    drag.samples.push({ t: event.timeStamp, x: drag.dx });
    if (drag.samples.length > 12) drag.samples.shift();
    paint(item.id, drag.dx);
  };
  const onUp = (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const d = drag;
    drag = null;
    if (d.axis !== "x") { if (swipeX.get(item.id)) snapBack(item.id, 0); return; }
    d.suppress = true;
    card._suppressClick = true;
    const velocity = releaseVelocity(d.samples);
    const dir = dismissDirection(d.dx, velocity);
    if (dir === 0) { snapBack(item.id, velocity); return; }
    dismiss(item.id, dir, Math.sign(velocity) === dir ? velocity : 0);
  };

  swipe.addEventListener("pointerdown", onDown);
  swipe.addEventListener("pointermove", onMove);
  swipe.addEventListener("pointerup", onUp);
  swipe.addEventListener("pointercancel", onUp);
  swipe.addEventListener("lostpointercapture", (event) => {
    if (event.target === swipe) onUp(event);
  });

  card.addEventListener("click", () => {
    if (card._suppressClick) { card._suppressClick = false; return; }
    if (!expanded()) { pinned = true; syncTimers(true); layout(); }
  });
  card.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && !expanded()) {
      event.preventDefault();
      pinned = true;
      syncTimers(true);
      layout();
      return;
    }
    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      if (event.repeat) return;
      dismiss(item.id, 1, 0);
    }
  });

  return li;
}

export function notify({ message, kind = "info", timeout = 6000, title } = {}) {
  mountNotifications();
  const tone = KINDS.has(kind) ? kind : "info";
  const item = {
    id: "n" + (++counter),
    kind: tone,
    title: title != null && String(title).trim() ? String(title) : TITLES[tone],
    body: message == null ? "" : String(message),
    createdAt: Date.now(),
    timeout: Math.max(0, Number(timeout) || 0),
    remaining: Math.max(0, Number(timeout) || 0),
    startedAt: Date.now(),
    pausedAt: 0,
    timer: 0,
    gone: false,
    leaving: false,
  };

  // Drop oldest if at limit (oldest is end, newest first)
  while (laidOut().length >= MAX_LIVE) {
    const live = laidOut();
    const oldest = live[live.length - 1];
    if (oldest) finishDismiss(oldest.id); else break;
  }

  const li = makeCard(item);
  items.unshift(item);
  listEl.prepend(li);

  if (announcerEl) {
    const msg = document.createElement("div");
    msg.textContent = item.title + ": " + item.body;
    announcerEl.append(msg);
    setTimeout(() => msg.remove(), 1000);
  }

  // If paused, don't start timer until pause ends
  if (isPaused()) {
    item.pausedAt = Date.now();
    item.startedAt = Date.now();
  } else {
    item.pausedAt = 0;
    item.startedAt = Date.now();
    arm(item);
  }

  syncTicker();
  measureAll();
  // Re-measure after paint for accurate heights
  requestAnimationFrame(() => { requestAnimationFrame(measureAll); });
  return item.id;
}
