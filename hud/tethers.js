import { api, h, s, icon } from "./lib.js";
import { li } from "./lineicons.js";
import { floatOpen } from "./float-pop.js";
import { anchorOn, tetherPath, localRect, ringLayout, resolveCollisions, ringSize, clampRect, MIN_GAP_REM, remPx } from "./tether-geometry.js";

// Tether pods around the composer (§3.1 / docs/design/23-tethers.md). Geometry lives in tether-geometry.js.
// Layout placement references (devin, cursor) live in tethers-layout.js compat; runtime uses ringLayout.
// Only providers detected in /api/status quotas.providers or active workers get boxes.

export function extractProviders(status) {
  const map = new Map();
  if (status && status.quotas && status.quotas.providers) {
    const qp = status.quotas.providers;
    if (Array.isArray(qp)) {
      for (const p of qp) {
        if (!p) continue;
        const id = typeof p === "string" ? p : (p.id || p.provider);
        if (!id) continue;
        const name = (typeof p === "object" && p.name) ? p.name : (id.charAt(0).toUpperCase() + id.slice(1));
        if (!name) continue;
        map.set(id, {
          id,
          name,
          state: (typeof p === "object" && p.state) ? p.state : "ready",
          workers: 0,
          dashed: false
        });
      }
    } else if (typeof qp === "object") {
      for (const [id, val] of Object.entries(qp)) {
        if (!id) continue;
        const obj = (typeof val === "object" && val !== null) ? val : {};
        const name = obj.name || (id.charAt(0).toUpperCase() + id.slice(1));
        if (!name) continue;
        map.set(id, { id, name, state: obj.state || "ready", workers: 0, dashed: false });
      }
    }
  }

  if (status && Array.isArray(status.workers)) {
    for (const w of status.workers) {
      if (!w) continue;
      const pid = w.provider || w.providerId;
      if (!pid) continue;
      if (!map.has(pid)) {
        const name = pid.charAt(0).toUpperCase() + pid.slice(1);
        if (!name) continue;
        map.set(pid, { id: pid, name, state: "busy", workers: 0, dashed: false });
      }
      map.get(pid).workers += 1;
    }
  }

  // Never render a pod unless the provider has both id and name.
  return Array.from(map.values()).filter((p) => p && p.id && p.name);
}

const STORAGE_PREFIX = "tt.pods.v1";

function deviceClass() {
  if (typeof window === "undefined") return "desktop";
  return window.innerWidth >= 1024 ? "desktop" : "tablet";
}

function loadPositions() {
  try {
    if (typeof localStorage === "undefined") return {};
    const key = `${STORAGE_PREFIX}.${deviceClass()}`;
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function savePositions(map) {
  try {
    if (typeof localStorage === "undefined") return;
    const key = `${STORAGE_PREFIX}.${deviceClass()}`;
    localStorage.setItem(key, JSON.stringify(map || {}));
  } catch {
    // Ignore storage errors
  }
}

const RETRACT_KEY = "tt.retract.v1";

function loadRetracted() {
  try {
    if (typeof localStorage === "undefined") return false;
    return localStorage.getItem(RETRACT_KEY) === "true";
  } catch {
    return false;
  }
}

function saveRetracted(b) {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(RETRACT_KEY, b ? "true" : "false");
  } catch {
    // Ignore storage errors
  }
}

const HIDDEN_KEY_PREFIX = "tt.pods.hidden.v1";

function loadHiddenPods() {
  try {
    if (typeof localStorage === "undefined") return new Set();
    const key = `${HIDDEN_KEY_PREFIX}.${deviceClass()}`;
    const raw = localStorage.getItem(key);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed : []);
  } catch {
    return new Set();
  }
}

function saveHiddenPods(set) {
  try {
    if (typeof localStorage === "undefined") return;
    const key = `${HIDDEN_KEY_PREFIX}.${deviceClass()}`;
    const arr = Array.from(set || []);
    localStorage.setItem(key, JSON.stringify(arr));
  } catch {
    // Ignore storage errors
  }
}

function prefersReducedMotion() {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

let activeHandle = null;

export function resetTethersPositions(handle) {
  if (!handle) return;
  handle.positions = {};
  savePositions(handle.positions);
  if (handle._physics) handle._physics.clear();
  handle._laidOut = false;
  if (typeof handle.redraw === "function") handle.redraw();
  if (typeof handle.layout === "function") handle.layout();
}

export function unmountTethers() {
  if (!activeHandle) return;
  const hnd = activeHandle;
  activeHandle = null;

  if (hnd._rafId) {
    try { cancelAnimationFrame(hnd._rafId); } catch {}
    hnd._rafId = null;
  }
  if (hnd._physics) hnd._physics.clear();
  if (hnd._ro) {
    try { hnd._ro.disconnect(); } catch {}
    hnd._ro = null;
  }
  if (hnd._onResize) {
    window.removeEventListener("resize", hnd._onResize);
    hnd._onResize = null;
  }
  if (hnd._onScroll) {
    window.removeEventListener("scroll", hnd._onScroll, true);
    hnd._onScroll = null;
  }
  if (hnd._onDocPointerDown) {
    document.removeEventListener("pointerdown", hnd._onDocPointerDown);
    hnd._onDocPointerDown = null;
  }
  if (hnd._mql && hnd._onMql) {
    try { hnd._mql.removeEventListener("change", hnd._onMql); } catch {
      try { hnd._mql.removeListener(hnd._onMql); } catch {}
    }
    hnd._mql = null;
    hnd._onMql = null;
  }
  if (hnd.podsWrap && hnd.podsWrap.parentNode) hnd.podsWrap.parentNode.removeChild(hnd.podsWrap);
  else if (hnd.podsBtn && hnd.podsBtn.parentNode) hnd.podsBtn.parentNode.removeChild(hnd.podsBtn);
  if (hnd.el && hnd.el.parentNode) hnd.el.parentNode.removeChild(hnd.el);
}

export function mountPodsButton(handle) {
  if (!handle || !handle.podsBtn) return;
  const composer = getComposer(handle);
  if (!composer) return;
  const cmpRoot = (typeof composer.closest === "function" ? composer.closest(".cmp") : null)
    || (typeof composer.querySelector === "function" ? composer.querySelector(".cmp") : null)
    || composer;
  if (!cmpRoot || typeof cmpRoot.querySelector !== "function") return;

  const principalBtn = cmpRoot.querySelector(".cmp-head-btn[title*='Principal'], button[aria-label*='Principal'], .cmp-head .cmp-head-btn");
  if (!principalBtn) return;

  const target = (typeof principalBtn.closest === "function" ? principalBtn.closest(".cmp-dd") : null) || principalBtn;
  const toInsert = handle.podsWrap || handle.podsBtn;
  if (target && target.parentNode && toInsert.nextSibling !== target) {
    if (typeof target.before === "function") {
      target.before(toInsert);
    } else {
      target.parentNode.insertBefore(toInsert, target);
    }
  }
}

export function updatePodsBadge(handle) {
  if (!handle || !handle.podsBtn) return;
  const hidden = handle.hiddenPods instanceof Set ? handle.hiddenPods : new Set(Array.isArray(handle.hiddenPods) ? handle.hiddenPods : []);
  const allProvs = handle._allProviders || [];
  const hiddenCount = allProvs.filter((p) => p && p.id && p.name && hidden.has(p.id)).length;
  const badge = handle.podsBtn.querySelector(".tt-pods-badge");
  if (badge) {
    badge.textContent = String(hiddenCount);
    badge.classList.toggle("tt-pods-badge-hidden", hiddenCount <= 0);
  }
}

function getComposer(handle) {
  let base = handle.anchor && handle.anchor.isConnected ? handle.anchor : null;
  if (!base && handle.el && handle.el.parentElement) {
    base = handle.el.parentElement.querySelector(".tt-composer-field") || handle.el.parentElement;
    if (base.classList && base.classList.contains("tt-composer-field")) handle.anchor = base;
  }
  base = base || handle.el;
  const outer = base && base.closest ? base.closest(".tt-sheet") : null;
  return outer || base;
}

function boundsOf(handle) {
  const r = handle.el.getBoundingClientRect();
  return { x: 0, y: 0, w: r.width || 0, h: r.height || 0 };
}

function redraw(handle) {
  if (!handle || !handle.el) return;
  mountPodsButton(handle);
  const composer = getComposer(handle);
  if (!composer || !handle.el.getBoundingClientRect().width) return;
  const layer = handle.el;
  const box = localRect(composer, layer);
  if (!box.w || !box.h) return;
  const reduced = prefersReducedMotion();
  for (const [id, podEl] of handle.pods.entries()) {
    if (!podEl || !podEl.isConnected) continue;
    const path = handle.paths ? handle.paths.get(id) : podEl.path;
    if (!path) continue;
    const pr = localRect(podEl, layer);
    const a = anchorOn(box, pr);
    const b = anchorOn(pr, box);
    const phys = handle._physics && handle._physics.get(id);
    const rest = (phys && phys.rest) || Math.hypot(b.x - a.x, b.y - a.y);
    path.setAttribute("d", tetherPath(a, b, rest, reduced));
  }
}

function ensurePhysicsLoop(handle) {
  if (!handle || handle._rafId) return;
  if (prefersReducedMotion()) return;

  const tick = () => {
    handle._rafId = null;
    let anyActive = false;
    const reduced = prefersReducedMotion();
    if (reduced) { redraw(handle); return; }
    const b = boundsOf(handle);
    if (!b.w || !b.h) return;

    for (const [id, podEl] of handle.pods.entries()) {
      const phys = handle._physics && handle._physics.get(id);
      if (!phys) continue;
      if (phys.dragging) anyActive = true;
      const dx = phys.targetX - phys.x;
      const dy = phys.targetY - phys.y;
      const dist = Math.hypot(dx, dy);
      const speed = Math.hypot(phys.vx, phys.vy);
      if (!phys.dragging && dist < 0.5 && speed < 0.5) {
        if (phys.x !== phys.targetX || phys.y !== phys.targetY || phys.vx !== 0 || phys.vy !== 0) {
          phys.x = phys.targetX; phys.y = phys.targetY; phys.vx = 0; phys.vy = 0;
          podEl.style.transform = `translate(${Math.round(phys.x)}px, ${Math.round(phys.y)}px)`;
          redraw(handle);
        }
        if (phys._needsSave) {
          phys._needsSave = false;
          handle.positions = handle.positions || {};
          handle.positions[id] = { x: Math.round(phys.x), y: Math.round(phys.y) };
          savePositions(handle.positions);
        }
        continue;
      }
      anyActive = true;
      // k = 0.18, damping = 0.78 per §6
      const k = 0.18;
      const damping = 0.78;
      phys.vx = (phys.vx + dx * k) * damping;
      phys.vy = (phys.vy + dy * k) * damping;
      phys.x += phys.vx;
      phys.y += phys.vy;
      const podW = podEl.offsetWidth || 120;
      const podH = podEl.offsetHeight || 56;
      const cl = clampRect({ x: phys.x, y: phys.y, w: podW, h: podH }, b);
      if (cl.x !== phys.x) phys.vx = 0;
      if (cl.y !== phys.y) phys.vy = 0;
      phys.x = cl.x; phys.y = cl.y;
      podEl.style.transform = `translate(${Math.round(phys.x)}px, ${Math.round(phys.y)}px)`;
      redraw(handle);
    }

    if (anyActive) handle._rafId = requestAnimationFrame(tick);
  };
  handle._rafId = requestAnimationFrame(tick);
}

function layout(handle) {
  if (!handle || !handle.el) return;
  const layer = handle.el;
  const composer = getComposer(handle);
  const b = boundsOf(handle);
  if (!b.w || !b.h || !composer) return;
  const box = localRect(composer, layer);
  if (!box.w || !box.h) return;
  const gap = MIN_GAP_REM * remPx() / 2;
  const podIds = Array.from(handle.pods.keys());
  if (!podIds.length) { redraw(handle); return; }

  const podsInfo = podIds.map((id) => {
    const el = handle.pods.get(id);
    return { id, w: el ? (el.offsetWidth || 140) : 140, h: el ? (el.offsetHeight || 56) : 56 };
  });
  const needH = ringSize(box, podsInfo, gap).h;
  if (needH > 0) layer.style.minHeight = `${Math.ceil(needH)}px`;

  handle.positions = handle.positions || {};
  let rects;
  const allSaved = podIds.every((id) => handle.positions[id] && Number.isFinite(handle.positions[id].x));
  if (allSaved && handle._laidOut) {
    rects = podsInfo.map((p) => ({ id: p.id, w: p.w, h: p.h, x: handle.positions[p.id].x, y: handle.positions[p.id].y }));
  } else if (allSaved && !handle._laidOut) {
    rects = podsInfo.map((p) => ({ id: p.id, w: p.w, h: p.h, x: handle.positions[p.id].x, y: handle.positions[p.id].y }));
    handle._laidOut = true;
  } else {
    rects = ringLayout(box, podsInfo, b, gap);
    rects = resolveCollisions(rects, [box], b, gap);
    for (const r of rects) handle.positions[r.id] = { x: Math.round(r.x), y: Math.round(r.y) };
    savePositions(handle.positions);
    handle._laidOut = true;
  }
  rects = resolveCollisions(rects, [box], b, gap);
  for (const r of rects) {
    handle.positions[r.id] = { x: Math.round(r.x), y: Math.round(r.y) };
    const podEl = handle.pods.get(r.id);
    if (!podEl) continue;
    if (!handle._physics) handle._physics = new Map();
    let phys = handle._physics.get(r.id);
    if (!phys) {
      phys = { x: r.x, y: r.y, vx: 0, vy: 0, targetX: r.x, targetY: r.y, dragging: false, rest: 0 };
      handle._physics.set(r.id, phys);
    } else if (!phys.dragging) {
      phys.targetX = r.x; phys.targetY = r.y;
      if (prefersReducedMotion()) { phys.x = r.x; phys.y = r.y; phys.vx = 0; phys.vy = 0; }
    }
    podEl.style.left = "0";
    podEl.style.top = "0";
    const cx = (phys && !prefersReducedMotion() && phys.dragging) ? phys.x : r.x;
    const cy = (phys && !prefersReducedMotion() && phys.dragging) ? phys.y : r.y;
    podEl.style.transform = `translate(${Math.round(cx)}px, ${Math.round(cy)}px)`;
  }
  // Compute rest lengths from initial anchors
  redraw(handle);
  for (const [id] of handle.pods.entries()) {
    const phys = handle._physics.get(id);
    const podEl = handle.pods.get(id);
    if (!phys || !podEl) continue;
    if (!phys.rest) {
      const pr = localRect(podEl, layer);
      const a = anchorOn(box, pr); const bb = anchorOn(pr, box);
      phys.rest = Math.hypot(bb.x - a.x, bb.y - a.y);
    }
  }
  redraw(handle);
}

function wirePodEvents(podEl, prov, handle) {
  let t0 = 0, x0 = 0, y0 = 0, moved = false, isDragging = false, longPressFired = false, longPressTimer = null;
  let grabOffsetX = 0, grabOffsetY = 0;
  let prevMoveTime = 0, prevClientX = 0, prevClientY = 0;

  podEl.addEventListener("pointerdown", (ev) => {
    try { podEl.setPointerCapture(ev.pointerId); } catch {}
    t0 = Date.now(); x0 = ev.clientX; y0 = ev.clientY; moved = false; isDragging = false; longPressFired = false;
    prevMoveTime = t0; prevClientX = x0; prevClientY = y0;
    if (!handle._physics) handle._physics = new Map();
    let phys = handle._physics.get(prov.id);
    if (!phys) {
      const pr = localRect(podEl, handle.el);
      phys = { x: pr.x, y: pr.y, vx: 0, vy: 0, targetX: pr.x, targetY: pr.y, dragging: false, rest: 0 };
      handle._physics.set(prov.id, phys);
    }
    const pt = localRect(podEl, handle.el);
    grabOffsetX = (ev.clientX - handle.el.getBoundingClientRect().left) - pt.x;
    grabOffsetY = (ev.clientY - handle.el.getBoundingClientRect().top) - pt.y;
    if (longPressTimer) clearTimeout(longPressTimer);
    longPressTimer = setTimeout(() => {
      if (!moved && !isDragging && t0) {
        longPressFired = true;
        handle.el.dispatchEvent(new CustomEvent("tt:principal", { bubbles: true, detail: { providerId: prov.id, name: prov.name } }));
      }
    }, 350);
  });

  podEl.addEventListener("pointermove", (ev) => {
    if (!t0) return;
    const now = Date.now();
    const dist = Math.hypot(ev.clientX - x0, ev.clientY - y0);
    if (!isDragging && dist >= 8) {
      moved = true; isDragging = true;
      podEl.classList.add("tt-pod-dragging");
      if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
    }
    if (isDragging) {
      const phys = handle._physics.get(prov.id);
      if (!phys) return;
      phys.dragging = true;
      const cr = handle.el.getBoundingClientRect();
      const podW = podEl.offsetWidth || 120, podH = podEl.offsetHeight || 56;
      const rawX = (ev.clientX - cr.left) - grabOffsetX;
      const rawY = (ev.clientY - cr.top) - grabOffsetY;
      const b = boundsOf(handle);
      const cl = clampRect({ x: rawX, y: rawY, w: podW, h: podH }, b);
      phys.targetX = cl.x; phys.targetY = cl.y;
      if (prefersReducedMotion()) {
        phys.x = phys.targetX; phys.y = phys.targetY; phys.vx = 0; phys.vy = 0;
        podEl.style.transform = `translate(${Math.round(phys.x)}px, ${Math.round(phys.y)}px)`;
        redraw(handle);
      } else {
        // Follow pointer directly while dragging; line rewrites every frame via rAF tick.
        phys.x = phys.targetX; phys.y = phys.targetY;
        podEl.style.transform = `translate(${Math.round(phys.x)}px, ${Math.round(phys.y)}px)`;
        redraw(handle);
      }
      prevMoveTime = now; prevClientX = ev.clientX; prevClientY = ev.clientY;
    }
  });

  const endDrag = (ev) => {
    if (!t0) return;
    try { podEl.releasePointerCapture(ev.pointerId); } catch {}
    if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
    const duration = Date.now() - t0;
    const dist = Math.hypot(ev.clientX - x0, ev.clientY - y0);
    t0 = 0;
    if (isDragging) {
      isDragging = false;
      podEl.classList.remove("tt-pod-dragging");
      const phys = handle._physics.get(prov.id);
      if (phys) {
        phys.dragging = false;
        const now = Date.now();
        const dt = now - prevMoveTime;
        let speedX = 0, speedY = 0, speed = 0;
        if (dt > 0 && dt < 150) {
          speedX = (ev.clientX - prevClientX) / dt;
          speedY = (ev.clientY - prevClientY) / dt;
          speed = Math.hypot(speedX, speedY);
        }
        const reduced = prefersReducedMotion();
        if (speed > 0.8 && !reduced) {
          phys.vx = speedX * 16; phys.vy = speedY * 16;
          const b = boundsOf(handle);
          const podW = podEl.offsetWidth || 120, podH = podEl.offsetHeight || 56;
          const cl = clampRect({ x: phys.x + speedX * 120, y: phys.y + speedY * 120, w: podW, h: podH }, b);
          phys.targetX = cl.x; phys.targetY = cl.y;
          phys._needsSave = true;
          handle.positions = handle.positions || {};
          handle.positions[prov.id] = { x: Math.round(phys.targetX), y: Math.round(phys.targetY) };
          savePositions(handle.positions);
          ensurePhysicsLoop(handle);
        } else {
          phys.vx = 0; phys.vy = 0; phys.x = phys.targetX; phys.y = phys.targetY;
          // After drop, resolve collisions so it never stays overlapping, then save.
          const composer = getComposer(handle);
          const layer = handle.el;
          const box = localRect(composer, layer);
          const b = boundsOf(handle);
          const gap = MIN_GAP_REM * remPx() / 2;
          const rects = Array.from(handle.pods.entries()).map(([id, el]) => {
            const p = handle._physics.get(id);
            return { id, w: el.offsetWidth || 120, h: el.offsetHeight || 56, x: p ? p.targetX : 0, y: p ? p.targetY : 0 };
          });
          resolveCollisions(rects, [box], b, gap);
          for (const r of rects) {
            const p = handle._physics.get(r.id);
            if (p) { p.x = r.x; p.y = r.y; p.targetX = r.x; p.targetY = r.y; }
            const el = handle.pods.get(r.id);
            if (el) el.style.transform = `translate(${Math.round(r.x)}px, ${Math.round(r.y)}px)`;
            handle.positions[r.id] = { x: Math.round(r.x), y: Math.round(r.y) };
          }
          savePositions(handle.positions);
          redraw(handle);
        }
      }
    } else if (dist < 8 && !moved) {
      if (duration < 250) {
        handle.el.dispatchEvent(new CustomEvent("tt:pod-open", { bubbles: true, detail: { providerId: prov.id, name: prov.name, anchor: podEl } }));
      } else if (duration >= 350 && !longPressFired) {
        handle.el.dispatchEvent(new CustomEvent("tt:principal", { bubbles: true, detail: { providerId: prov.id, name: prov.name } }));
      }
    }
    moved = false;
  };

  podEl.addEventListener("pointerup", endDrag);
  podEl.addEventListener("pointercancel", endDrag);
  podEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
      ev.preventDefault();
      handle.el.dispatchEvent(new CustomEvent("tt:pod-open", { bubbles: true, detail: { providerId: prov.id, name: prov.name, anchor: podEl } }));
    }
  });
}

export function renderTethers(handle, status) {
  if (!handle) return;
  handle.status = status;
  const list = extractProviders(status);
  handle._allProviders = list;

  const hidden = handle.hiddenPods instanceof Set ? handle.hiddenPods : new Set(Array.isArray(handle.hiddenPods) ? handle.hiddenPods : []);
  const visibleList = list.filter((p) => p && p.id && p.name && !hidden.has(p.id));
  const currentIds = new Set(visibleList.map((p) => p.id));

  for (const [id, podEl] of Array.from(handle.pods.entries())) {
    if (!currentIds.has(id)) {
      if (podEl && podEl.parentNode) podEl.parentNode.removeChild(podEl);
      const pathEl = handle.paths ? handle.paths.get(id) : (podEl && podEl.path);
      if (pathEl && pathEl.parentNode) pathEl.parentNode.removeChild(pathEl);
      handle.pods.delete(id);
      if (handle.paths) handle.paths.delete(id);
      if (handle._physics) handle._physics.delete(id);
    }
  }

  for (const prov of visibleList) {
    if (!prov || !prov.id || !prov.name) continue; // never render placeholder/empty pod
    const isCrown = Boolean(status && (status.principal === prov.id || status.principalId === prov.id));
    const workerCount = prov.workers || 0;
    if (handle.pods.has(prov.id)) {
      const podEl = handle.pods.get(prov.id);
      podEl.provider = prov;
      podEl.setAttribute("data-provider-name", prov.name);
      podEl.classList.toggle("tt-pod-crown", isCrown);
      const badge = podEl.querySelector(".tt-pod-badge");
      if (badge) { badge.textContent = String(workerCount); badge.classList.toggle("tt-pod-badge-hidden", workerCount <= 0); }
      const meta = podEl.querySelector(".tt-pod-meta");
      if (meta) meta.textContent = prov.dashed ? "Not connected yet" : (prov.state || "");
    } else {
      let podClass = "tt-pod";
      if (prov.dashed) podClass += " tt-pod-dashed";
      if (isCrown) podClass += " tt-pod-crown";
      const badgeClass = "tt-pod-badge" + (workerCount > 0 ? "" : " tt-pod-badge-hidden");
      const iconBg = `var(--series-${prov.id}, var(--pv-${prov.id}, var(--surface-2, var(--bg))))`;
      const podEl = h("div", { class: podClass, "data-provider": prov.id, role: "button", tabindex: "0" },
        h("div", { class: "tt-pod-icon", style: `background:${iconBg}` }, prov.name.charAt(0).toUpperCase()),
        h("div", { class: "tt-pod-label", title: prov.name }, prov.name),
        h("div", { class: badgeClass }, String(workerCount)),
        h("div", { class: "tt-pod-meta" }, prov.dashed ? "Not connected yet" : (prov.state || ""))
      );
      podEl.provider = prov;
      podEl.setAttribute("data-provider-name", prov.name);
      const pathClass = prov.dashed ? "tt-tether tt-tether-dashed" : "tt-tether";
      const pathEl = s("path", { class: pathClass });
      podEl.path = pathEl;
      handle.svg.appendChild(pathEl);
      handle.el.appendChild(podEl);
      handle.pods.set(prov.id, podEl);
      if (handle.paths) handle.paths.set(prov.id, pathEl);
      wirePodEvents(podEl, prov, handle);
      handle._laidOut = false; // new pod needs ring placement
    }
  }

  const totalWorkers = list.reduce((acc, p) => acc + (p.workers || 0), 0);
  if (handle.retractBtn) {
    handle.retractBtn.hidden = totalWorkers <= 0;
    if (handle.el.contains(handle.retractBtn)) handle.el.appendChild(handle.retractBtn);
  }
  mountPodsButton(handle);
  updatePodsBadge(handle);
  if (handle.isPodsMenuOpen && typeof handle._renderPodsMenu === "function") handle._renderPodsMenu();

  layout(handle);
}

export function mountTethers(host, opts = {}) {
  unmountTethers();

  const el = h("div", { class: "tt-container " + (opts.docked ? "tt-docked" : "tt-overlay") });
  // SVG in CSS pixels, no viewBox scaling. position:absolute; inset:0; overflow:visible; pointer-events:none
  const svg = s("svg", { class: "tt-svg" });
  el.appendChild(svg);

  const retractBtn = h("button", { class: "tt-retract", type: "button", "aria-pressed": "false", "aria-label": "Hide providers", hidden: "true" });
  el.appendChild(retractBtn);

  const podsBtn = h("button", {
    class: "cmp-head-btn tt-pods-menu-btn",
    type: "button",
    "aria-haspopup": "true",
    "aria-expanded": "false",
    "aria-label": "Pods",
    title: "Pods visibility"
  });

  let podsIcon;
  try {
    podsIcon = typeof icon === "function" ? icon("stack") : null;
  } catch {}
  if (!podsIcon || !podsIcon.querySelector || !podsIcon.querySelector("path[d]")) {
    try { podsIcon = li("stack"); } catch {}
  }
  const podsLabel = h("span", { class: "cmp-lbl" }, "Pods");
  const podsBadge = h("span", { class: "tt-pods-badge tt-pods-badge-hidden" }, "0");
  if (podsIcon) podsBtn.appendChild(podsIcon);
  podsBtn.appendChild(podsLabel);
  podsBtn.appendChild(podsBadge);

  const podsPopover = h("div", { class: "tt-pods-popover", role: "dialog", "aria-label": "Pods visibility", hidden: "true" });
  const podsWrap = h("div", { class: "cmp-dd tt-pods-dd" }, podsBtn, podsPopover);

  const initialRetracted = loadRetracted();
  if (initialRetracted) {
    el.classList.add("tt-retracted");
    retractBtn.setAttribute("aria-pressed", "true");
    retractBtn.setAttribute("aria-label", "Show providers");
  }

  const handle = {
    el, svg, retractBtn, podsWrap, podsBtn, podsPopover,
    hiddenPods: loadHiddenPods(),
    isPodsMenuOpen: false,
    pods: new Map(), paths: new Map(), _physics: new Map(),
    positions: loadPositions(),
    status: opts.status || null,
    anchor: opts.anchor || null,
    retracted: initialRetracted,
    opts,
    redraw: () => redraw(handle),
  };

  retractBtn.addEventListener("click", () => { handle.setRetracted(!handle.retracted); });
  handle.setRetracted = (b) => {
    handle.retracted = Boolean(b);
    saveRetracted(handle.retracted);
    handle.el.classList.toggle("tt-retracted", handle.retracted);
    handle.retractBtn.setAttribute("aria-pressed", handle.retracted ? "true" : "false");
    handle.retractBtn.setAttribute("aria-label", handle.retracted ? "Show providers" : "Hide providers");
  };

  handle.setPodHidden = (id, bool) => {
    if (!(handle.hiddenPods instanceof Set)) handle.hiddenPods = new Set(Array.isArray(handle.hiddenPods) ? handle.hiddenPods : []);
    if (bool) handle.hiddenPods.add(id); else handle.hiddenPods.delete(id);
    saveHiddenPods(handle.hiddenPods);
    renderTethers(handle, handle.status);
    updatePodsBadge(handle);
    if (handle.isPodsMenuOpen && typeof handle._renderPodsMenu === "function") handle._renderPodsMenu();
  };

  const renderPodsMenu = () => {
    while (podsPopover.firstChild) podsPopover.removeChild(podsPopover.firstChild);
    const providers = handle._allProviders || extractProviders(handle.status) || [];
    const valid = providers.filter((p) => p && p.id && p.name);
    if (!valid.length) { podsPopover.appendChild(h("div", { class: "tt-pods-item tt-pods-empty" }, "No providers detected")); return; }
    const hidden = handle.hiddenPods instanceof Set ? handle.hiddenPods : new Set();
    for (const prov of valid) {
      const checkbox = h("input", { type: "checkbox", class: "tt-pods-toggle", "aria-label": `Show ${prov.name}` });
      checkbox.checked = !hidden.has(prov.id);
      checkbox.addEventListener("change", () => { handle.setPodHidden(prov.id, !checkbox.checked); });
      podsPopover.appendChild(h("label", { class: "tt-pods-item switch-row", "data-provider-id": prov.id }, h("span", { class: "tt-pods-item-name" }, prov.name), h("span", { class: "switch" }, checkbox, h("span", { class: "slider" }))));
    }
  };
  handle._renderPodsMenu = renderPodsMenu;

  handle.togglePodsMenu = (force) => {
    const next = typeof force === "boolean" ? force : !handle.isPodsMenuOpen;
    handle.isPodsMenuOpen = next;
    if (next) {
      renderPodsMenu(); podsPopover.removeAttribute("hidden"); podsBtn.setAttribute("aria-expanded", "true");
      if (handle._unfloat) handle._unfloat();
      handle._unfloat = floatOpen(podsPopover, podsBtn, { align: "end" });
      const firstInput = podsPopover.querySelector("input");
      if (firstInput && typeof firstInput.focus === "function") firstInput.focus();
    } else { podsPopover.setAttribute("hidden", "true"); podsBtn.setAttribute("aria-expanded", "false"); if (handle._unfloat) { handle._unfloat(); handle._unfloat = null; } }
    return next;
  };
  podsBtn.addEventListener("click", (ev) => { ev.stopPropagation(); handle.togglePodsMenu(); });

  const onMenuKeyDown = (ev) => {
    if (ev.key === "Escape" && handle.isPodsMenuOpen) {
      ev.stopPropagation(); handle.togglePodsMenu(false);
      if (podsBtn && typeof podsBtn.focus === "function") podsBtn.focus();
    }
  };
  podsWrap.addEventListener("keydown", onMenuKeyDown);
  podsPopover.addEventListener("keydown", onMenuKeyDown);
  const onDocPointerDown = (ev) => { if (handle.isPodsMenuOpen && podsWrap && !podsWrap.contains(ev.target) && !podsPopover.contains(ev.target)) handle.togglePodsMenu(false); };
  document.addEventListener("pointerdown", onDocPointerDown);
  handle._onDocPointerDown = onDocPointerDown;

  handle.update = (st) => renderTethers(handle, st);
  handle.layout = () => layout(handle);
  handle.dropTargetAt = (clientX, clientY) => {
    for (const [id, node] of handle.pods.entries()) {
      const elNode = node && node.el ? node.el : node;
      if (!elNode || !elNode.getBoundingClientRect) continue;
      const r = elNode.getBoundingClientRect();
      if (clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom) return id;
    }
    return null;
  };
  handle.destroy = () => unmountTethers();

  activeHandle = handle;
  host.appendChild(el);

  renderTethers(handle, opts.status || null);

  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => { if (!getComposer(handle).isConnected) getComposer(handle); redraw(handle); layout(handle); });
    ro.observe(el);
    const composer = getComposer(handle);
    if (composer && composer !== el) ro.observe(composer);
    handle._ro = ro;
  }

  const onResize = () => { layout(handle); };
  window.addEventListener("resize", onResize);
  handle._onResize = onResize;

  const onScroll = () => redraw(handle);
  window.addEventListener("scroll", onScroll, true);
  handle._onScroll = onScroll;

  if (typeof matchMedia === "function") {
    const mql = matchMedia("(prefers-reduced-motion: reduce)");
    const onMql = () => { layout(handle); redraw(handle); };
    try { mql.addEventListener("change", onMql); } catch { try { mql.addListener(onMql); } catch {} }
    handle._mql = mql; handle._onMql = onMql;
  }

  if (!opts.status) {
    handle.ready = api("/api/status").then((st) => { renderTethers(handle, st); return handle; }).catch(() => handle);
  } else {
    handle.ready = Promise.resolve(handle);
  }

  const vv = window.visualViewport;
  const narrow = window.matchMedia("(max-width: 40rem)");
  if (vv) {
    const onVV = () => {
      const up = narrow.matches && vv.height < window.innerHeight * 0.75;
      el.classList.toggle("tt-keyboard", up);
      el.style.setProperty("--tt-kb-bottom", up ? `${Math.max(0, window.innerHeight - vv.height - vv.offsetTop)}px` : "0px");
      [...el.querySelectorAll(".tt-pod")].forEach((p, i) => p.style.setProperty("--tt-chip-i", String(i)));
    };
    vv.addEventListener("resize", onVV);
    vv.addEventListener("scroll", onVV);
    onVV();
  }

  return handle;
}
