/* Shared motion: the settings stack panel's curve and timings (stackpanel.js), for pages and cards. */
export const EASE = "cubic-bezier(0.23, 1, 0.32, 1)";
const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function enter(el) {
  if (!el || reduced()) return;
  el.animate([{ opacity: 0, transform: "translateX(-24px)" }, { opacity: 1, transform: "none" }], { duration: 280, easing: EASE });
}

export function stagger(parent) {
  if (!parent || reduced()) return;
  [...parent.children].forEach((c, i) => c.style.setProperty("--mo-i", String(Math.min(i, 8))));
  parent.classList.remove("mo-stagger");
  void parent.offsetWidth;
  parent.classList.add("mo-stagger");
  setTimeout(() => parent.classList.remove("mo-stagger"), 280 + 8 * 40 + 50);
}

/* A page change: the new content enters the way a settings page does. Never call this from the poll re-render. */
export function pageSwap(container) {
  if (!container || reduced()) return;
  enter(container);
  stagger(container);
}
