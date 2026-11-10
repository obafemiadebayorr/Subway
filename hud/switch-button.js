/* Switch button — vanilla port of kokonutui/switch-button (MIT, @dorianbaffier).
   The HUD's two-state toggle: dark mode in the topbar, god mode and every
   boolean flag in the settings panel.
   - themeSwitch(opts): the full component; the caller owns the theme.
   - switchButton(opts): the generic on/off pill every other switch uses.
   - switchFace(checked): the non-interactive glyph for rows that are
     themselves buttons (the stack panel), so no button nests a button.

   The new theme spreads from the button as a circle (View Transitions API).
   Without that API, or with reduced motion, the change is instant. */


const SIZES = {
  sm: { btn: 32, icon: 14, text: 13 },
  default: { btn: 36, icon: 16, text: 14 },
  lg: { btn: 40, icon: 18, text: 15 },
};

const EASE_OUT = "cubic-bezier(0.23, 1, 0.32, 1)";
const EASE_IN_OUT = "cubic-bezier(0.65, 0, 0.35, 1)";
const EASE_REVEAL = "cubic-bezier(0.4, 0, 0.2, 1)";
const REVEAL_ATTR = "data-theme-reveal";
const REVEAL_STYLE_ID = "subway-theme-reveal";
const REVEAL_CSS = `html[${REVEAL_ATTR}]::view-transition-old(root),html[${REVEAL_ATTR}]::view-transition-new(root){animation:none;mix-blend-mode:normal}`;

const RAYS = [0, 45, 90, 135, 180, 225, 270, 315].map((deg) => {
  const rad = (deg * Math.PI) / 180;
  const at = (r) => ({
    x: (12 + r * Math.cos(rad)).toFixed(2),
    y: (12 + r * Math.sin(rad)).toFixed(2),
  });
  return { deg, from: at(7.5), to: at(10) };
});

const MORPH_PROPS = ["opacity", "scale", "translate", "filter"];

function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

let revealRunning = false;

function ensureRevealStyle() {
  if (document.getElementById(REVEAL_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = REVEAL_STYLE_ID;
  style.textContent = REVEAL_CSS;
  document.head.appendChild(style);
}

/* Snapshot the animated parts, then play them from there to their new
   (class-driven) computed state — the same trick the React version uses, so
   the morph keeps working even while the theme swap rebuilds styles. */
function snapshot(parts) {
  const map = new Map();
  for (const el of parts) {
    const style = getComputedStyle(el);
    const frame = { offset: 0 };
    for (const prop of MORPH_PROPS) frame[prop] = style.getPropertyValue(prop);
    map.set(el, frame);
    for (const animation of el.getAnimations()) animation.cancel();
  }
  return map;
}

function timing(el, toDark) {
  const { part, index = "0" } = el.dataset;
  const i = Number(index);
  switch (part) {
    case "ray":
      return { delay: toDark ? i * 18 : 140 + i * 22, duration: 260, easing: EASE_OUT };
    case "bite":
      return { delay: toDark ? 60 : 0, duration: 380, easing: EASE_IN_OUT };
    case "disc":
      return { delay: toDark ? 40 : 0, duration: 380, easing: EASE_OUT };
    default:
      return { duration: 240, easing: EASE_OUT };
  }
}

function morph(from, toDark) {
  if (reducedMotion()) return;
  for (const [el, frame] of from) {
    el.animate([frame, {}], { ...timing(el, toDark), fill: "backwards" });
  }
}

function revealFrom(button, apply) {
  if (typeof document.startViewTransition !== "function" || reducedMotion() || revealRunning) {
    apply();
    return;
  }
  const rect = button.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
  const root = document.documentElement;
  ensureRevealStyle();
  root.setAttribute(REVEAL_ATTR, "");
  revealRunning = true;
  let transition;
  try {
    transition = document.startViewTransition(apply);
  } catch (e) {
    root.removeAttribute(REVEAL_ATTR);
    revealRunning = false;
    apply();
    return;
  }
  transition.ready.then(() => {
    root.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
      { duration: 500, easing: EASE_REVEAL, pseudoElement: "::view-transition-new(root)" },
    );
  }).catch(() => { /* skipped or aborted */ });
  transition.finished.catch(() => { /* the theme is applied by now */ }).finally(() => {
    root.removeAttribute(REVEAL_ATTR);
    revealRunning = false;
  });
}

let maskSeq = 0;

/* The sun/moon icon: a disc whose "bite" circle slides away in dark mode and
   whose rays scale away, all class-driven so CSS owns the resting state. */
function iconSvg(size) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.classList.add("ksb-icon");
  svg.setAttribute("aria-hidden", "true");
  const maskId = `ksb-moon-${++maskSeq}`;
  const mask = document.createElementNS("http://www.w3.org/2000/svg", "mask");
  mask.id = maskId;
  const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  rect.setAttribute("x", "-4"); rect.setAttribute("y", "-4");
  rect.setAttribute("width", "32"); rect.setAttribute("height", "32");
  rect.setAttribute("fill", "white");
  mask.appendChild(rect);
  const bite = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  bite.setAttribute("cx", "17"); bite.setAttribute("cy", "7"); bite.setAttribute("r", "7.5");
  bite.setAttribute("fill", "black");
  bite.setAttribute("data-part", "bite");
  bite.classList.add("ksb-bite");
  mask.appendChild(bite);
  svg.appendChild(mask);
  const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
  g.setAttribute("mask", `url(#${maskId})`);
  const disc = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  disc.setAttribute("cx", "12"); disc.setAttribute("cy", "12"); disc.setAttribute("r", "4.75");
  disc.setAttribute("fill", "currentColor");
  disc.setAttribute("data-part", "disc");
  disc.classList.add("ksb-disc");
  g.appendChild(disc);
  svg.appendChild(g);
  const rays = document.createElementNS("http://www.w3.org/2000/svg", "g");
  rays.setAttribute("stroke", "currentColor");
  rays.setAttribute("stroke-linecap", "round");
  rays.setAttribute("stroke-width", "2");
  RAYS.forEach((ray, index) => {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", ray.from.x); line.setAttribute("y1", ray.from.y);
    line.setAttribute("x2", ray.to.x); line.setAttribute("y2", ray.to.y);
    line.setAttribute("data-part", "ray");
    line.setAttribute("data-index", String(index));
    line.classList.add("ksb-ray");
    rays.appendChild(line);
  });
  svg.appendChild(rays);
  return svg;
}

/* onText/offText are the words shown inside the pill for the checked and
   unchecked state — every call site passes its own, nothing is baked in here. */
function labelSpan(onText, offText) {
  const wrap = document.createElement("span");
  wrap.className = "ksb-label";
  wrap.setAttribute("aria-hidden", "true");
  const off = document.createElement("span");
  off.className = "ksb-label-off";
  off.setAttribute("data-part", "label");
  off.textContent = offText;
  const on = document.createElement("span");
  on.className = "ksb-label-on";
  on.setAttribute("data-part", "label");
  on.textContent = onText;
  wrap.append(off, on);
  return wrap;
}

/* Ionicons 5 'toggle' (MIT, https://ionic.io/ionicons) */
const KNOB_ICON = `<path d="M368 112H144C64.6 112 0 176.6 0 256s64.6 144 144 144h224c79.4 0 144-64.6 144-144s-64.6-144-144-144zm0 256a112 112 0 11112-112 112.12 112.12 0 01-112 112z"/>`;

function knobIcon(size) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 512 512");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "currentColor");
  svg.setAttribute("class", "ksb-icon ksb-knob-icon");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = KNOB_ICON;
  return svg;
}

function parts(container) {
  return [...container.querySelectorAll("[data-part]")];
}

function setDark(rootEl, dark) {
  rootEl.classList.toggle("dark", dark);
  rootEl.classList.toggle("is-on", dark);
  rootEl.setAttribute("aria-pressed", String(dark));
}

function buildFace(dark, size, withLabel, labelText, iconFn = iconSvg, onText = "Dark", offText = "Light") {
  const sizeCss = SIZES[size] || SIZES.default;
  const root = document.createElement("span");
  root.className = `ksb ksb-${size}`;
  root.classList.toggle("is-on", Boolean(dark));
  if (iconFn === iconSvg) setDark(root, Boolean(dark));
  root.appendChild(iconFn(sizeCss.icon));
  if (withLabel) root.appendChild(labelSpan(onText, offText));
  if (labelText) {
    const t = document.createElement("span");
    t.className = "ksb-text";
    t.textContent = labelText;
    root.appendChild(t);
  }
  return root;
}

export function themeSwitch(opts = {}) {
  const { size = "default", showLabel = true, reveal = true, getTheme, onThemeChange } = opts;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = `ksb ksb-${SIZES[size] ? size : "default"}`;
  btn.setAttribute("aria-label", "Dark theme");
  const sizeCss = SIZES[size] || SIZES.default;
  btn.appendChild(iconSvg(sizeCss.icon));
  if (showLabel) btn.appendChild(labelSpan("Dark", "Light"));

  let dark = false;
  const paint = () => {
    dark = getTheme ? getTheme() === "dark" : dark;
    setDark(btn, dark);
  };
  paint();

  btn.addEventListener("click", () => {
    const from = getTheme ? getTheme() : (dark ? "dark" : "light");
    const next = from === "dark" ? "light" : "dark";
    const apply = () => {
      const before = snapshot(parts(btn));
      if (onThemeChange) onThemeChange(next);
      dark = next === "dark";
      setDark(btn, dark);
      morph(before, dark);
    };
    if (reveal) revealFrom(btn, apply);
    else apply();
  });
  return btn;
}

/* The one switch every boolean flag in the HUD renders through: a labelled
   pill, same shape as the theme switch, with the generic knob glyph and an
   onLabel/offLabel pair that defaults to "On"/"Off". Click, Space and Enter
   (native button activation) and a post-refresh `__set` all funnel through
   the same `paint` setter, so the label, aria-checked and knob never drift
   apart. */
export function switchButton(opts = {}) {
  const { checked = false, onChange, size = "sm", showLabel = true, onLabel = "On", offLabel = "Off", ariaLabel } = opts;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = `ksb ksb-${SIZES[size] ? size : "sm"}`;
  btn.setAttribute("role", "switch");
  if (ariaLabel) btn.setAttribute("aria-label", ariaLabel);
  const sizeCss = SIZES[size] || SIZES.sm;
  btn.appendChild(knobIcon(sizeCss.icon));
  if (showLabel) btn.appendChild(labelSpan(onLabel, offLabel));

  const paint = (on) => {
    const active = Boolean(on);
    btn.classList.toggle("is-on", active);
    btn.setAttribute("aria-checked", String(active));
  };
  paint(checked);

  btn.addEventListener("click", () => {
    const next = !btn.classList.contains("is-on");
    paint(next);
    if (onChange) onChange(next);
  });
  btn.__set = paint;
  return btn;
}

/* Decorative face for rows that are themselves buttons (stack panel flags). */
export function switchFace(checked) {
  return buildFace(Boolean(checked), "sm", true, "", knobIcon, "On", "Off");
}
