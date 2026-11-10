/* Activity terrain — port of kokonutui/activity-terrain (MIT, @dorianbaffier).
   An isometric block terrain drawn on a canvas: it tilts up from flat when it
   comes on screen, spins with a flick, and lifts the column you point at.
   Vanilla port of the React component; the maths is unchanged. */

const DAYS = 7;
const GAP = 0.18;
const MAX_HEIGHT = 4.2;
const DEG = Math.PI / 180;
const PITCH_TERRAIN = 56 * DEG;
const YAW_TERRAIN = 18 * DEG;
const YAW_LIMIT = 38 * DEG;
const PITCH_LIMIT = 66 * DEG;
const DRAG_YAW = 0.32 * DEG;
const DRAG_PITCH = 0.36 * DEG;
const SPIN_HALF_LIFE = 0.16;
const SPIN_IN = (YAW_TERRAIN * Math.LN2) / SPIN_HALF_LIFE;
const PITCH_STIFFNESS = 140;
const PITCH_DAMPING = 2 * Math.sqrt(PITCH_STIFFNESS);
const EDGE_STIFFNESS = 180;
const BUMP_STIFFNESS = 400;
const BUMP_DAMPING = 2 * Math.sqrt(BUMP_STIFFNESS) * 0.7;
const BUMP = 0.45;
const WAVE = 0.7;
const PAD = { left: 46, right: 26, top: 26, bottom: 24 };
const MIN_HEIGHT = 96;
const MAX_HEIGHT_CSS = 340;
const REST_EPSILON = 0.0005;
const LABELS = ["Mon", "", "Wed", "", "Fri", "", ""];

const clamp = (v, min, max) => Math.min(max, Math.max(min, v));

/* The spin-in intro plays once per page load. The overview re-renders on every
   status poll, and a fresh widget must mount settled, not shoot again. */
let introPlayed = false;

function easeOutBack(t) {
  const c = 1.4;
  const u = t - 1;
  return 1 + (c + 1) * u * u * u + c * u * u;
}

function rubber(value, min, max) {
  if (value < min) return min - 0.18 * (1 - Math.exp((value - min) / 0.18));
  if (value > max) return max + 0.18 * (1 - Math.exp((max - value) / 0.18));
  return value;
}

function parseDay(value) {
  const [y, m, d] = String(value).split("-").map(Number);
  return new Date(Date.UTC(y, (m || 1) - 1, d || 1));
}

function buildCells(data, endDate) {
  const end = parseDay(endDate);
  const start = new Date(end.getTime() - (data.length - 1) * 86400000);
  const offset = (start.getUTCDay() + 6) % 7;
  const max = Math.max(1, ...data);
  return data.map((count, index) => {
    const slot = index + offset;
    const norm = count / max;
    return {
      index,
      col: Math.floor(slot / DAYS),
      row: slot % DAYS,
      count,
      level: count === 0 ? 0 : Math.ceil(norm * 4),
      height: 0.06 + norm ** 0.85 * MAX_HEIGHT,
      date: new Date(start.getTime() + index * 86400000),
    };
  });
}

function hexToRgb(value) {
  const hex = String(value || "").trim().replace("#", "");
  if (hex.length !== 3 && hex.length !== 6) return [136, 136, 136];
  const n = parseInt(hex.length === 3 ? hex.replace(/./g, "$&$&") : hex, 16);
  if (!Number.isFinite(n)) return [136, 136, 136];
  return [Math.floor(n / 65536) % 256, Math.floor(n / 256) % 256, n % 256];
}

function shade(rgb, amount) {
  const k = 1 - amount;
  return `rgb(${Math.round(rgb[0] * k)},${Math.round(rgb[1] * k)},${Math.round(rgb[2] * k)})`;
}

function readPalette(node) {
  const style = getComputedStyle(node);
  const read = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
  return {
    levels: [0, 1, 2, 3, 4].map((l) => hexToRgb(read(`--terrain-${l}`, "#888888"))),
    accent: hexToRgb(read("--terrain-accent", "#5094d1")),
    text: read("--terrain-text", "#8b8f95"),
    font: style.fontFamily,
  };
}

function project(pose, x, y, z) {
  const cos = Math.cos(pose.yaw);
  const sin = Math.sin(pose.yaw);
  const xr = x * cos - z * sin;
  const zr = x * sin + z * cos;
  return [
    pose.cx + xr * pose.scale,
    pose.cy + (zr * Math.cos(pose.pitch) - y * Math.sin(pose.pitch)) * pose.scale,
  ];
}

function visibleFaces(pose, x0, z0, h) {
  const x1 = x0 + 1 - GAP;
  const z1 = z0 + 1 - GAP;
  const p = (x, y, z) => project(pose, x, y, z);
  const faces = [];
  const sin = Math.sin(pose.yaw);
  const cos = Math.cos(pose.yaw);
  if (h > 0.02 && pose.pitch > 0.01) {
    if (cos > 0) {
      faces.push({ points: [p(x0, 0, z1), p(x1, 0, z1), p(x1, h, z1), p(x0, h, z1)], tone: 0.22 });
    } else {
      faces.push({ points: [p(x0, 0, z0), p(x1, 0, z0), p(x1, h, z0), p(x0, h, z0)], tone: 0.22 });
    }
    if (sin > 0.001) {
      faces.push({ points: [p(x1, 0, z0), p(x1, 0, z1), p(x1, h, z1), p(x1, h, z0)], tone: 0.38 });
    } else if (sin < -0.001) {
      faces.push({ points: [p(x0, 0, z0), p(x0, 0, z1), p(x0, h, z1), p(x0, h, z0)], tone: 0.38 });
    }
  }
  faces.push({ points: [p(x0, h, z0), p(x1, h, z0), p(x1, h, z1), p(x0, h, z1)], tone: 0 });
  return faces;
}

function inside(points, x, y) {
  let hit = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [xi, yi] = points[i];
    const [xj, yj] = points[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

function polygon(ctx, points) {
  ctx.beginPath();
  ctx.moveTo(points[0][0], points[0][1]);
  for (let i = 1; i < points.length; i++) ctx.lineTo(points[i][0], points[i][1]);
  ctx.closePath();
}

const formatDay = (date) =>
  date.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
const formatMonth = (date) =>
  date.toLocaleDateString("en-US", { month: "short", timeZone: "UTC" });

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

/* One terrain instance. Accepts `rows` (objects with day, spun, finished, tokens) or `data` (counts). */
function create({ rows, data, endDate, title, defaultFlat = false, noun = "worker", verb = "spun" }) {
  const normRows = rows && rows.length ? rows : (data || []).map((v) => (typeof v === "object" ? v : { spun: Number(v) || 0 }));
  const counts = normRows.map((r) => Number(r.spun) || 0);
  let cells = buildCells(counts, endDate);
  cells.forEach((cell, i) => {
    const r = normRows[i] || {};
    cell.day = r.day || formatDay(cell.date);
    cell.spun = Number(r.spun) || 0;
    cell.finished = Number(r.finished) || 0;
    cell.tokens = Number(r.tokens) || 0;
  });

  let weeks = (cells[cells.length - 1]?.col ?? 0) + 1;
  let total = counts.reduce((a, b) => a + b, 0);
  let flat = defaultFlat;

  const root = el("div", "terrain-card");
  const headRow = el("div", "terrain-head");
  const headText = el("div", "terrain-headtext");
  const heading = el("p", "terrain-title", title || "Activity");
  const readout = el("p", "terrain-readout");
  readout.setAttribute("aria-live", "polite");
  headText.appendChild(heading);
  headText.appendChild(readout);
  headRow.appendChild(headText);

  const modes = el("div", "terrain-modes");
  const flatBtn = el("button", "terrain-mode", "Flat");
  const terrBtn = el("button", "terrain-mode", "Terrain");
  [flatBtn, terrBtn].forEach((b) => {
    b.type = "button";
    modes.appendChild(b);
  });
  headRow.appendChild(modes);

  const scrollWrap = el("div", "terrain-scroll");
  const canvas = el("canvas", "terrain-canvas");
  canvas.tabIndex = 0;
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", `${title || "Activity"}: ${total} ${noun}s ${verb} over ${weeks} weeks. Use the arrow keys to read each day.`);
  scrollWrap.appendChild(canvas);

  const detailRow = el("div", "terrain-detail");
  detailRow.style.display = "none";

  const foot = el("div", "terrain-foot");
  foot.appendChild(el("span", "terrain-less", "Less"));
  [0, 1, 2, 3, 4].forEach((l) => {
    const chip = el("span", "terrain-swatch");
    chip.style.background = `var(--terrain-${l})`;
    foot.appendChild(chip);
  });
  foot.appendChild(el("span", "terrain-more", "More"));

  root.appendChild(headRow);
  root.appendChild(scrollWrap);
  root.appendChild(detailRow);
  root.appendChild(foot);

  // Mobile: keep newest weeks visible first
  requestAnimationFrame(() => {
    if (scrollWrap.scrollWidth > scrollWrap.clientWidth) {
      scrollWrap.scrollLeft = scrollWrap.scrollWidth - scrollWrap.clientWidth;
    }
  });

  /* A replacement widget (status-poll re-render) mounts already tilted, so the
     re-render never flashes a flat frame or replays the intro. */
  const settled = introPlayed && !defaultFlat;
  const sim = {
    yaw: settled ? YAW_TERRAIN : 0,
    yawSpeed: 0,
    pitch: settled ? PITCH_TERRAIN : 0,
    pitchSpeed: 0,
    pitchTarget: defaultFlat ? 0 : PITCH_TERRAIN,
    bumps: new Float64Array(cells.length),
    bumpSpeeds: new Float64Array(cells.length),
    hovered: -1,
    origin: { col: 0, row: 3 },
    width: 0,
    height: 0,
    dpr: 1,
    drag: null,
    raf: 0,
    last: 0,
    visible: false,
    started: false,
    reduceMotion: false,
  };
  let palette = null;
  let active = null;
  let dragging = false;

  const reduceQuery = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
  sim.reduceMotion = Boolean(reduceQuery && reduceQuery.matches);

  /* Column exaggeration shrinks as the grid widens, so a full year fills the
     card as a low landscape instead of towering over it. */
  function heightScale() {
    return clamp(14 / Math.max(weeks, 1), 0.28, 1);
  }

  function columnHeight(cell, lift) {
    const span = Math.hypot(weeks, DAYS) || 1;
    const d = Math.hypot(cell.col - sim.origin.col, cell.row - sim.origin.row) / span;
    const local = clamp(lift * (1 + WAVE) - d * WAVE, 0, 1);
    return cell.height * easeOutBack(local) * heightScale() + sim.bumps[cell.index] * heightScale();
  }

  /* Screen bounds of the whole grid at scale 1, for any camera. */
  function footprint(yaw, pitch, lift) {
    const w = weeks;
    const corners = [[0, 0], [w, 0], [0, DAYS], [w, DAYS]];
    const probe = { yaw, pitch, scale: 1, cx: 0, cy: 0 };
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const [x, z] of corners) {
      for (const y of [0, MAX_HEIGHT * heightScale() * Math.min(lift, 1)]) {
        const [px, py] = project(probe, x - w / 2, y, z - DAYS / 2);
        minX = Math.min(minX, px);
        maxX = Math.max(maxX, px);
        minY = Math.min(minY, py);
        maxY = Math.max(maxY, py);
      }
    }
    return { minX, maxX, minY, maxY };
  }

  /* The canvas height that lets the grid run the full width of its card, so a
     year of days does not sit as a small block in a wide empty panel. */
  function heightForWidth(width) {
    if (!width || !weeks) return MIN_HEIGHT;
    const b = footprint(YAW_TERRAIN, PITCH_TERRAIN, 1);
    const dw = Math.max(b.maxX - b.minX, 0.001);
    const dh = Math.max(b.maxY - b.minY, 0.001);
    const scale = (width - PAD.left - PAD.right) / dw;
    return Math.round(clamp(dh * scale + PAD.top + PAD.bottom + 4, MIN_HEIGHT, MAX_HEIGHT_CSS));
  }

  function pose() {
    const lift = clamp(sim.pitch / PITCH_TERRAIN, 0, 1.2);
    const { minX, maxX, minY, maxY } = footprint(sim.yaw, sim.pitch, lift);
    const innerW = Math.max(sim.width - PAD.left - PAD.right, 40);
    const innerH = Math.max(sim.height - PAD.top - PAD.bottom, 40);
    const scale = Math.min(innerW / (maxX - minX), innerH / (maxY - minY));
    const cx = PAD.left + innerW / 2 - ((minX + maxX) / 2) * scale;
    const cy = PAD.top + innerH / 2 - ((minY + maxY) / 2) * scale;
    return {
      yaw: sim.yaw,
      pitch: sim.pitch,
      scale,
      cx,
      cy,
      lift,
      left: cx + minX * scale,
      top: cy + minY * scale,
    };
  }

  function ordered(p) {
    const sin = Math.sin(p.yaw);
    const cos = Math.cos(p.yaw);
    const depth = (c) => (c.col + 0.5 - weeks / 2) * sin + (c.row + 0.5 - DAYS / 2) * cos;
    return cells.slice().sort((a, b) => depth(a) - depth(b));
  }

  function drawLabels(ctx, p, lift, pal) {
    const alpha = clamp(1 - lift * 1.6, 0, 1);
    if (alpha === 0) return;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = pal.text;
    ctx.font = `11px ${pal.font}`;
    ctx.textBaseline = "middle";
    /* Months ride a straight line above the grid, weekdays a straight column to
       its left, so neither ends up squeezed against the card edge. */
    const monthY = p.top - 10;
    ctx.textAlign = "left";
    let month = -1;
    for (const cell of cells) {
      if (cell.row !== 0 && cell.index !== 0) continue;
      const m = cell.date.getUTCMonth();
      if (m !== month && cell.col < weeks - 1) {
        month = m;
        const [x] = project(p, cell.col - weeks / 2, 0, -DAYS / 2);
        if (x > 4 && x < sim.width - PAD.right) ctx.fillText(formatMonth(cell.date), x, monthY);
      }
    }
    const labelX = p.left - 8;
    ctx.textAlign = "right";
    for (let row = 0; row < DAYS; row++) {
      if (!LABELS[row]) continue;
      const [, y] = project(p, -weeks / 2, 0, row + 0.4 - DAYS / 2);
      if (y > 6 && y < sim.height - 4) ctx.fillText(LABELS[row], labelX, y);
    }
    ctx.globalAlpha = 1;
  }

  function draw() {
    const ctx = canvas.getContext("2d");
    if (!ctx || !sim.width) return;
    if (!palette) palette = readPalette(canvas);
    const p = pose();
    ctx.setTransform(sim.dpr, 0, 0, sim.dpr, 0, 0);
    ctx.clearRect(0, 0, sim.width, sim.height);
    drawLabels(ctx, p, p.lift, palette);
    ctx.lineJoin = "round";
    for (const cell of ordered(p)) {
      const h = columnHeight(cell, p.lift);
      const color = cell.index === sim.hovered ? palette.accent : palette.levels[cell.level];
      for (const face of visibleFaces(p, cell.col - weeks / 2, cell.row - DAYS / 2, h)) {
        polygon(ctx, face.points);
        const fill = shade(color, face.tone);
        ctx.fillStyle = fill;
        ctx.fill();
        ctx.strokeStyle = fill;
        ctx.lineWidth = 0.6;
        ctx.stroke();
      }
    }
  }

  function hitTest(x, y) {
    const p = pose();
    const list = ordered(p);
    for (let i = list.length - 1; i >= 0; i--) {
      const cell = list[i];
      const h = columnHeight(cell, p.lift);
      for (const face of visibleFaces(p, cell.col - weeks / 2, cell.row - DAYS / 2, h)) {
        if (inside(face.points, x, y)) return cell;
      }
    }
    return null;
  }

  function stepSprings(dt) {
    let energy = 0;
    if (!sim.drag) {
      sim.yawSpeed *= 0.5 ** (dt / SPIN_HALF_LIFE);
      let target = sim.yaw;
      if (sim.pitchTarget === 0) target = 0;
      else if (sim.yaw > YAW_LIMIT) target = YAW_LIMIT;
      else if (sim.yaw < -YAW_LIMIT) target = -YAW_LIMIT;
      if (target !== sim.yaw) {
        sim.yawSpeed += ((target - sim.yaw) * EDGE_STIFFNESS - sim.yawSpeed * 2 * Math.sqrt(EDGE_STIFFNESS)) * dt;
      }
      sim.yaw += sim.yawSpeed * dt;
      energy = Math.max(energy, Math.abs(sim.yawSpeed), Math.abs(target - sim.yaw));

      const force = (sim.pitchTarget - sim.pitch) * PITCH_STIFFNESS - sim.pitchSpeed * PITCH_DAMPING;
      sim.pitchSpeed += force * dt;
      sim.pitch += sim.pitchSpeed * dt;
      energy = Math.max(energy, Math.abs(sim.pitchSpeed), Math.abs(sim.pitchTarget - sim.pitch));
    }
    for (let i = 0; i < sim.bumps.length; i++) {
      const target = i === sim.hovered && sim.pitch > 0.1 ? BUMP : 0;
      sim.bumpSpeeds[i] += ((target - sim.bumps[i]) * BUMP_STIFFNESS - sim.bumpSpeeds[i] * BUMP_DAMPING) * dt;
      sim.bumps[i] += sim.bumpSpeeds[i] * dt;
      energy = Math.max(energy, Math.abs(sim.bumpSpeeds[i]) * 0.1, Math.abs(target - sim.bumps[i]));
    }
    return energy;
  }

  function tick(now) {
    sim.raf = 0;
    if (!canvas.isConnected) { sim.last = 0; return; }
    const dt = sim.last ? Math.min((now - sim.last) / 1000, 1 / 30) : 1 / 60;
    sim.last = now;
    const energy = stepSprings(dt);
    draw();
    if ((sim.drag || energy > REST_EPSILON) && sim.visible) {
      sim.raf = requestAnimationFrame(tick);
    } else {
      sim.last = 0;
    }
  }

  function turnIn() {
    if (Math.abs(sim.yaw) > 2 * DEG) return;
    if (sim.reduceMotion) sim.yaw = YAW_TERRAIN;
    else sim.yawSpeed = SPIN_IN;
  }

  function wake() {
    if (sim.reduceMotion) {
      sim.pitch = sim.pitchTarget;
      sim.yaw = clamp(sim.yaw, -YAW_LIMIT, YAW_LIMIT);
      sim.pitchSpeed = 0;
      sim.yawSpeed = 0;
      sim.bumps.fill(0);
      draw();
      return;
    }
    if (!sim.raf && sim.visible) sim.raf = requestAnimationFrame(tick);
  }

  function setView(nextFlat, origin) {
    flat = nextFlat;
    sim.origin = origin || { col: nextFlat ? weeks : 0, row: 3 };
    sim.pitchTarget = nextFlat ? 0 : PITCH_TERRAIN;
    sim.yawSpeed = 0;
    if (!nextFlat) turnIn();
    wake();
    paintModes();
    paintReadout();
  }

  function paintModes() {
    flatBtn.setAttribute("aria-pressed", String(flat));
    terrBtn.setAttribute("aria-pressed", String(!flat));
    flatBtn.classList.toggle("on", flat);
    terrBtn.classList.toggle("on", !flat);
  }

  function formatTokens(t) {
    if (t >= 1e6) return `${(t / 1e6).toFixed(1)}M tokens`;
    if (t >= 1e3) return `${(t / 1e3).toFixed(1)}k tokens`;
    return `${t} tokens`;
  }

  function paintReadout() {
    if (active) {
      const dayStr = active.day || formatDay(active.date);
      readout.textContent = `${dayStr}: ${active.spun} ${verb} · ${active.finished} finished · ${formatTokens(active.tokens)}`;
      detailRow.style.display = "none";
    } else {
      readout.textContent = `${total.toLocaleString("en-US")} ${noun}s ${verb} in the last ${weeks} week${weeks === 1 ? "" : "s"}`;
      detailRow.style.display = "none";
      detailRow.innerHTML = "";
    }
  }

  flatBtn.addEventListener("click", () => flat !== true && setView(true));
  terrBtn.addEventListener("click", () => flat !== false && setView(false));

  function localPoint(event) {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function hover(cell) {
    const index = cell ? cell.index : -1;
    if (index !== sim.hovered) {
      sim.hovered = index;
      active = cell;
      paintReadout();
      if (sim.reduceMotion) draw();
      else wake();
    }
  }

  canvas.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const { x, y } = localPoint(event);
    try { canvas.setPointerCapture(event.pointerId); } catch { /* synthetic pointer */ }
    const cell = hitTest(x, y);
    if (cell) sim.origin = { col: cell.col, row: cell.row };
    sim.drag = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      yaw: sim.yaw,
      pitch: sim.pitch,
      moved: false,
      samples: [{ t: event.timeStamp, x: event.clientX, y: event.clientY }],
    };
    sim.yawSpeed = 0;
    sim.pitchSpeed = 0;
    dragging = true;
    canvas.classList.add("grabbing");
    hover(cell);
    wake();
  });

  canvas.addEventListener("pointermove", (event) => {
    const drag = sim.drag;
    if (!drag || drag.id !== event.pointerId) {
      if (event.pointerType === "mouse") {
        const { x, y } = localPoint(event);
        hover(hitTest(x, y));
      }
      return;
    }
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) > 4) drag.moved = true;
    if (!drag.moved) return;
    sim.yaw = rubber(drag.yaw + dx * DRAG_YAW, -YAW_LIMIT, YAW_LIMIT);
    sim.pitch = rubber(drag.pitch + dy * DRAG_PITCH, 0, PITCH_LIMIT);
    drag.samples.push({ t: event.timeStamp, x: event.clientX, y: event.clientY });
    if (drag.samples.length > 5) drag.samples.shift();
    if (sim.reduceMotion) draw();
    else wake();
  });

  function endDrag(event) {
    const drag = sim.drag;
    if (!drag || drag.id !== event.pointerId) return;
    sim.drag = null;
    dragging = false;
    canvas.classList.remove("grabbing");
    if (!drag.moved) {
      wake();
      return;
    }
    const first = drag.samples[0];
    const lastSample = drag.samples[drag.samples.length - 1] || first;
    const dt = Math.max((lastSample.t - first.t) / 1000, 0.016);
    const vx = (lastSample.x - first.x) / dt;
    const vy = (lastSample.y - first.y) / dt;
    const recent = event.timeStamp - lastSample.t < 80;
    sim.yawSpeed = recent ? vx * DRAG_YAW : 0;
    sim.pitchSpeed = recent ? vy * DRAG_PITCH : 0;
    const projected = sim.pitch + sim.pitchSpeed * 0.15;
    const nextFlat = projected < PITCH_TERRAIN / 2;
    flat = nextFlat;
    sim.pitchTarget = nextFlat ? 0 : PITCH_TERRAIN;
    paintModes();
    paintReadout();
    wake();
  }

  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("lostpointercapture", endDrag);
  canvas.addEventListener("pointerleave", (event) => {
    if (!sim.drag && event.pointerType === "mouse") hover(null);
  });
  canvas.addEventListener("blur", () => {
    if (!sim.drag) hover(null);
  });
  canvas.addEventListener("keydown", (event) => {
    const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const move = moves[event.key];
    if (!move) return;
    event.preventDefault();
    const current = cells[sim.hovered] || cells[cells.length - 1];
    if (!current) return;
    const next = cells.find((c) => c.col === current.col + move[0] && c.row === current.row + move[1]);
    hover(next || current);
  });

  let appliedHeight = 0;
  /* Size the box to the grid so a year of days fills its card. The observer
     re-fires once after this; that second pass does the real draw. */
  function refit() {
    const width = sim.width || canvas.clientWidth;
    const want = heightForWidth(width);
    if (!want || Math.abs(want - appliedHeight) < 3) return;
    appliedHeight = want;
    canvas.style.height = want + "px";
  }

  const resize = new ResizeObserver(([entry]) => {
    sim.width = Math.round(entry.contentRect.width);
    sim.height = Math.round(entry.contentRect.height);
    sim.dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(sim.width * sim.dpr);
    canvas.height = Math.round(sim.height * sim.dpr);
    refit();
    draw();
  });
  resize.observe(canvas);

  let onScreen = false;
  function update() {
    sim.visible = onScreen && !document.hidden;
    if (sim.visible) {
      if (!sim.started) {
        sim.started = true;
        if (sim.pitchTarget > 0) {
          if (!introPlayed) {
            introPlayed = true;
            turnIn();
          } else {
            /* Already introduced once: mount settled instead of replaying. */
            sim.pitch = sim.pitchTarget;
            sim.yaw = YAW_TERRAIN;
            sim.pitchSpeed = 0;
            sim.yawSpeed = 0;
          }
        }
      }
      wake();
    } else if (sim.raf) {
      cancelAnimationFrame(sim.raf);
      sim.raf = 0;
      sim.last = 0;
    }
  }
  const intersection = new IntersectionObserver(([entry]) => {
    onScreen = entry.isIntersecting;
    update();
  });
  intersection.observe(canvas);
  document.addEventListener("visibilitychange", update);

  const repaint = () => {
    palette = null;
    draw();
  };
  const themeWatch = new MutationObserver(repaint);
  themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
  const scheme = typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null;
  if (scheme) scheme.addEventListener("change", repaint);
  if (reduceQuery) reduceQuery.addEventListener("change", (e) => { sim.reduceMotion = e.matches; });

  function updateData(next, nextEnd) {
    const normRows = next && next.length ? next : [];
    const counts = normRows.map((v) => (typeof v === "object" ? Number(v.spun) || 0 : Number(v) || 0));
    cells = buildCells(counts, nextEnd);
    cells.forEach((cell, i) => {
      const r = typeof normRows[i] === "object" ? normRows[i] : { spun: Number(normRows[i]) || 0 };
      cell.day = r.day || formatDay(cell.date);
      cell.spun = Number(r.spun) || 0;
      cell.finished = Number(r.finished) || 0;
      cell.tokens = Number(r.tokens) || 0;
    });
    weeks = (cells[cells.length - 1]?.col ?? 0) + 1;
    total = counts.reduce((a, b) => a + b, 0);
    sim.bumps = new Float64Array(cells.length);
    sim.bumpSpeeds = new Float64Array(cells.length);
    sim.hovered = -1;
    active = null;
    canvas.setAttribute("aria-label", `${title || "Activity"}: ${total} ${noun}s ${verb} over ${weeks} weeks. Use the arrow keys to read each day.`);
    paintReadout();
    appliedHeight = 0; /* the grid changed shape: refit the card */
    refit();
    wake();
    draw();
  }

  function destroy() {
    resize.disconnect();
    intersection.disconnect();
    themeWatch.disconnect();
    if (scheme) scheme.removeEventListener("change", repaint);
    document.removeEventListener("visibilitychange", update);
    cancelAnimationFrame(sim.raf);
  }

  paintModes();
  paintReadout();
  root.__terrain = { updateData, destroy };
  return root;
}

/* One terrain per overview render: re-render just updates the counts so the
   intro animation does not restart on every poll. */
let cached = null;

export function activityTerrain({ rows = [], data = [], endDate, title = "Activity terrain", defaultFlat = false, noun = "worker", verb = "spun" } = {}) {
  const dataPayload = rows.length ? rows : data;
  if (!dataPayload.length) endDate = endDate || new Date().toISOString().slice(0, 10);
  if (cached && cached.root.isConnected) {
    cached.root.__terrain.updateData(dataPayload, endDate);
    return cached.root;
  }
  if (cached) cached.root.__terrain.destroy();
  const root = create({ rows, data, endDate, title, defaultFlat, noun, verb });
  cached = { root };
  return root;
}

export default activityTerrain;
